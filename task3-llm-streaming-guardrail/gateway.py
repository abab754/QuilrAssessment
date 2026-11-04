import logging
import sys

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


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Proxy endpoint that accepts chat completion requests."""
    body = await request.json()
    logger.info("Received chat completion request with %d messages", len(body.get("messages", [])))

    # TODO: forward to upstream LLM provider and stream back with PII redaction
    return {"status": "received", "messages": len(body.get("messages", []))}
