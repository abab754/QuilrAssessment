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
