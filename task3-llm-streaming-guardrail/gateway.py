import json
import logging
import re
import sys

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

UPSTREAM_URL = "http://localhost:8001"

app = FastAPI(title="LLM Streaming Guardrail")


def extract_text_from_sse(line: str) -> str | None:
    """Pull the delta content text out of an SSE data line. Returns None for non-content lines."""
    if not line.startswith("data: "):
        return None
    payload = line[6:]
    if payload.strip() == "[DONE]":
        return None
    try:
        data = json.loads(payload)
        return data["choices"][0]["delta"].get("content")
    except (json.JSONDecodeError, KeyError, IndexError):
        return None


def wrap_as_sse(text: str) -> str:
    """Wrap a text string back into an SSE chunk in OpenAI format."""
    data = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
    return f"data: {json.dumps(data)}\n\n"


PII_PATTERNS = [
    # Email: word chars, dots, pluses, hyphens @ domain
    re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
    # SSN: 3-2-4 digits with hyphens or spaces
    re.compile(r"\b\d{3}[-\s]\d{2}[-\s]\d{4}\b"),
    # Credit card: 13-19 digits with optional hyphens or spaces
    re.compile(r"\b(?:\d[-\s]?){13,19}\b"),
]

# Characters that could be part of an in-progress PII pattern.
# If the buffer ends with any of these, we hold it back in case
# the next chunk completes a match.
PARTIAL_CHARS = re.compile(r"[a-zA-Z0-9._%+@-]+$")


class PIIRedactor:
    """Streaming PII redactor that handles patterns split across chunks.

    Maintains a buffer of text that might be part of an incomplete PII pattern.
    Flushes safe text immediately and holds back trailing characters that could
    be the start of a match.
    """

    def __init__(self):
        self.buffer = ""

    def feed(self, text: str) -> str:
        """Add new text. Returns the portion that is safe to emit."""
        self.buffer += text
        # Redact any complete PII matches in the buffer
        self.buffer = self._redact(self.buffer)

        # Find trailing text that could be a partial PII match.
        # Hold it back; flush everything before it.
        match = PARTIAL_CHARS.search(self.buffer)
        if match and match.end() == len(self.buffer):
            safe = self.buffer[: match.start()]
            self.buffer = self.buffer[match.start():]
            return safe

        # No partial match at the end — flush everything
        safe = self.buffer
        self.buffer = ""
        return safe

    def flush(self) -> str:
        """Flush remaining buffer (end of stream). Redact anything left."""
        remaining = self._redact(self.buffer)
        self.buffer = ""
        return remaining

    def _redact(self, text: str) -> str:
        for pattern in PII_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text


async def stream_with_redaction(upstream_response: httpx.Response):
    """Read SSE chunks from upstream, redact PII, and stream back."""
    redactor = PIIRedactor()

    async for line in upstream_response.aiter_lines():
        if not line:
            continue

        text = extract_text_from_sse(line)
        if text is not None:
            safe = redactor.feed(text)
            if safe:
                yield wrap_as_sse(safe)
        elif line.startswith("data: [DONE]"):
            # Flush any remaining buffered text before closing
            remaining = redactor.flush()
            if remaining:
                yield wrap_as_sse(remaining)
            yield "data: [DONE]\n\n"
        else:
            yield f"{line}\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy endpoint — forwards to upstream LLM and streams response back."""
    body = await request.json()
    logger.info("Received chat completion request with %d messages", len(body.get("messages", [])))

    client = httpx.AsyncClient()
    upstream_response = await client.stream(
        "POST", f"{UPSTREAM_URL}/v1/chat/completions", json=body
    ).__aenter__()

    async def generate():
        try:
            async for chunk in stream_with_redaction(upstream_response):
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(generate(), media_type="text/event-stream")
