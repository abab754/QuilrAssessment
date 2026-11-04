"""Mock LLM server that streams SSE chunks, sometimes containing PII."""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="Mock LLM Provider")

# Each scenario is a list of text chunks the "LLM" streams back
SCENARIOS = {
    "clean": [
        "The weather today is ",
        "sunny with a high of ",
        "75 degrees. Enjoy your day!",
    ],
    "email": [
        "You can reach our support team at ",
        "john.doe@secret",
        "company.com for assistance.",
    ],
    "ssn": [
        "The customer's SSN is ",
        "123-45",
        "-6789 on file.",
    ],
    "credit_card": [
        "Payment was made with card ",
        "4111-1111-",
        "1111-1111 ending soon.",
    ],
    "mixed": [
        "Contact alice@example.com ",
        "or call about SSN 987-65",
        "-4321 and card 5500-0000",
        "-0000-0004 thanks.",
    ],
}


def make_sse_chunk(text: str, index: int, finish: bool = False) -> str:
    """Format a text chunk as an SSE event matching OpenAI's streaming format."""
    if finish:
        data = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    else:
        data = {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
    return f"data: {json.dumps(data)}\n\n"


async def stream_chunks(chunks: list[str]):
    for i, chunk in enumerate(chunks):
        yield make_sse_chunk(chunk, i)
        await asyncio.sleep(0.05)
    yield make_sse_chunk("", 0, finish=True)
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()

    # Pick scenario from the first message content, default to "clean"
    first_msg = body.get("messages", [{}])[0].get("content", "clean")
    scenario = first_msg if first_msg in SCENARIOS else "clean"

    return StreamingResponse(
        stream_chunks(SCENARIOS[scenario]),
        media_type="text/event-stream",
    )
