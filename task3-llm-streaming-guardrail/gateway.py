import json
import logging
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


async def stream_with_redaction(upstream_response: httpx.Response):
    """Read SSE chunks from upstream, and stream them back to the client.

    TODO: step 4 will add PII redaction in the middle.
    """
    async for line in upstream_response.aiter_lines():
        if not line:
            continue

        text = extract_text_from_sse(line)
        if text is not None:
            yield wrap_as_sse(text)
        elif line.startswith("data: [DONE]"):
            yield "data: [DONE]\n\n"
        else:
            # Pass through non-content SSE lines (e.g. finish_reason)
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
