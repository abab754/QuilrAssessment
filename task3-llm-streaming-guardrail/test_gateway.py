import json

import pytest
from httpx import ASGITransport, AsyncClient

from mock_llm import app as mock_app


async def collect_stream(response) -> str:
    """Read all SSE chunks from a streaming response and concatenate the text."""
    full_text = ""
    async for line in response.aiter_lines():
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            data = json.loads(payload)
            content = data["choices"][0]["delta"].get("content", "")
            full_text += content
        except (json.JSONDecodeError, KeyError, IndexError):
            pass
    return full_text


def make_request(scenario: str) -> dict:
    return {"messages": [{"role": "user", "content": scenario}], "stream": True}


# ============================================================
# Unit tests for PIIRedactor (no server needed)
# ============================================================

from gateway import PIIRedactor


class TestPIIRedactorUnit:
    def test_email_in_single_chunk(self):
        r = PIIRedactor()
        safe = r.feed("Contact john@example.com for help.")
        safe += r.flush()
        assert "john@example.com" not in safe
        assert "[REDACTED]" in safe

    def test_ssn_in_single_chunk(self):
        r = PIIRedactor()
        safe = r.feed("SSN is 123-45-6789 on file.")
        safe += r.flush()
        assert "123-45-6789" not in safe
        assert "[REDACTED]" in safe

    def test_credit_card_in_single_chunk(self):
        r = PIIRedactor()
        safe = r.feed("Card 4111-1111-1111-1111 used.")
        safe += r.flush()
        assert "4111-1111-1111-1111" not in safe
        assert "[REDACTED]" in safe

    def test_email_split_across_chunks(self):
        r = PIIRedactor()
        out = r.feed("Contact john.doe@secret")
        out += r.feed("company.com for help.")
        out += r.flush()
        assert "john.doe@secretcompany.com" not in out
        assert "[REDACTED]" in out

    def test_ssn_split_across_chunks(self):
        r = PIIRedactor()
        out = r.feed("SSN is 123-45")
        out += r.feed("-6789 on file.")
        out += r.flush()
        assert "123-45-6789" not in out
        assert "[REDACTED]" in out

    def test_credit_card_split_across_chunks(self):
        r = PIIRedactor()
        out = r.feed("Card 4111-1111-")
        out += r.feed("1111-1111 done.")
        out += r.flush()
        assert "4111-1111-1111-1111" not in out
        assert "[REDACTED]" in out

    def test_clean_text_passes_through(self):
        r = PIIRedactor()
        out = r.feed("Hello world. ")
        out += r.feed("Nice day today. ")
        out += r.flush()
        assert "Hello world." in out
        assert "Nice day today." in out
        assert "[REDACTED]" not in out

    def test_multiple_pii_in_one_chunk(self):
        r = PIIRedactor()
        out = r.feed("Email a@b.co and SSN 111-22-3333 here.")
        out += r.flush()
        assert "a@b.co" not in out
        assert "111-22-3333" not in out
        assert out.count("[REDACTED]") == 2

    def test_flush_on_empty_buffer(self):
        r = PIIRedactor()
        assert r.flush() == ""


# ============================================================
# Integration tests: mock LLM stream -> redactor pipeline
# ============================================================

from gateway import stream_with_redaction


async def stream_scenario(scenario: str) -> str:
    """Hit the mock LLM for a scenario and run the response through the redactor."""
    transport = ASGITransport(app=mock_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        upstream_resp = await client.send(
            client.build_request("POST", "/v1/chat/completions", json=make_request(scenario)),
            stream=True,
        )
        full_text = ""
        async for sse_line in stream_with_redaction(upstream_resp):
            if not sse_line.startswith("data: "):
                continue
            payload = sse_line[6:].strip()
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
                full_text += data["choices"][0]["delta"].get("content", "")
            except (json.JSONDecodeError, KeyError, IndexError):
                pass
        await upstream_resp.aclose()
        return full_text


@pytest.mark.anyio
async def test_clean_stream_no_redaction():
    text = await stream_scenario("clean")
    assert "sunny" in text
    assert "[REDACTED]" not in text


@pytest.mark.anyio
async def test_email_redacted_in_stream():
    text = await stream_scenario("email")
    assert "@" not in text
    assert "[REDACTED]" in text


@pytest.mark.anyio
async def test_ssn_redacted_in_stream():
    text = await stream_scenario("ssn")
    assert "123-45-6789" not in text
    assert "[REDACTED]" in text


@pytest.mark.anyio
async def test_credit_card_redacted_in_stream():
    text = await stream_scenario("credit_card")
    assert "4111" not in text
    assert "[REDACTED]" in text


@pytest.mark.anyio
async def test_mixed_pii_all_redacted():
    text = await stream_scenario("mixed")
    assert "@" not in text
    assert "987-65-4321" not in text
    assert "5500" not in text
    assert text.count("[REDACTED]") >= 3


# ============================================================
# Evaluation criteria tests
# ============================================================


class TestStreamChunkingAndBufferManagement:
    """Criterion 1: Efficient async stream chunking and buffer state management."""

    def test_safe_text_emitted_incrementally(self):
        """Redactor emits safe text on each feed(), not just at flush()."""
        r = PIIRedactor()
        out1 = r.feed("Hello world. ")
        out2 = r.feed("How are you? ")
        out3 = r.flush()
        # Safe text should be emitted in feed() calls, not all at flush
        assert len(out1) > 0 or len(out2) > 0
        assert out1 + out2 + out3 == "Hello world. How are you? "

    def test_buffer_only_holds_trailing_partial(self):
        """After feed(), buffer only contains the trailing partial match candidate."""
        r = PIIRedactor()
        r.feed("Safe text here. Then some partial 123-45")
        # Buffer should only hold "123-45", not the entire input
        assert len(r.buffer) < 20
        assert "Safe text" not in r.buffer

    def test_buffer_empty_after_safe_text(self):
        """Buffer is empty after feeding text that ends with a space (no partial)."""
        r = PIIRedactor()
        r.feed("No PII here at all. ")
        assert r.buffer == ""

    def test_multiple_chunks_buffer_stays_small(self):
        """Buffer stays bounded even after many chunks of safe text."""
        r = PIIRedactor()
        for _ in range(100):
            r.feed("This is a perfectly safe sentence. ")
        assert len(r.buffer) == 0


class TestRegexPerformance:
    """Criterion 2: Performant string/regex pattern matching over partial text streams."""

    def test_buffer_does_not_grow_with_safe_text(self):
        """Regex runs on small buffer, not accumulating text."""
        r = PIIRedactor()
        for i in range(50):
            r.feed(f"Chunk {i} with no PII. ")
            # Buffer should never hold more than the trailing partial
            assert len(r.buffer) < 50, f"Buffer grew too large at chunk {i}: {len(r.buffer)}"

    def test_regex_handles_many_chunks_without_slowdown(self):
        """Process 1000 chunks quickly - proves regex isn't re-scanning old text."""
        import time
        r = PIIRedactor()
        start = time.perf_counter()
        for i in range(1000):
            r.feed(f"Safe chunk number {i}. ")
        r.flush()
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, f"Processing 1000 chunks took {elapsed:.2f}s"

    def test_partial_pattern_does_not_cause_backtracking(self):
        """Repeated partial-looking strings don't cause regex backtracking issues."""
        r = PIIRedactor()
        total_out = ""
        for _ in range(100):
            total_out += r.feed("abc-de ")
        total_out += r.flush()
        assert "[REDACTED]" not in total_out


class TestMemoryAndLatency:
    """Criterion 3: Memory efficiency and low-latency proxying design."""

    @pytest.mark.anyio
    async def test_first_chunk_arrives_before_stream_ends(self):
        """TTFT: first redacted chunk is yielded before the full stream completes."""
        transport = ASGITransport(app=mock_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            upstream_resp = await client.send(
                client.build_request("POST", "/v1/chat/completions", json=make_request("clean")),
                stream=True,
            )
            chunks = []
            async for sse_line in stream_with_redaction(upstream_resp):
                if not sse_line.startswith("data: "):
                    continue
                payload = sse_line[6:].strip()
                if payload == "[DONE]":
                    break
                chunks.append(payload)
            await upstream_resp.aclose()
        # Multiple chunks, not one big blob
        assert len(chunks) >= 2, f"Expected multiple chunks, got {len(chunks)}"

    def test_no_full_response_accumulation(self):
        """Redactor never holds the full response, only a small trailing buffer."""
        r = PIIRedactor()
        total_fed = 0
        max_buffer = 0
        for i in range(200):
            chunk = f"This is sentence number {i} in a long response. "
            r.feed(chunk)
            total_fed += len(chunk)
            max_buffer = max(max_buffer, len(r.buffer))
        r.flush()
        assert total_fed > 5000
        assert max_buffer < 100, f"Buffer peaked at {max_buffer} chars"

    def test_redactor_memory_is_constant(self):
        """Buffer size is bounded regardless of how much text flows through."""
        r = PIIRedactor()
        buffer_sizes = []
        for i in range(500):
            r.feed("Normal text with no PII patterns whatsoever. ")
            buffer_sizes.append(len(r.buffer))
        r.flush()
        assert max(buffer_sizes) == 0
