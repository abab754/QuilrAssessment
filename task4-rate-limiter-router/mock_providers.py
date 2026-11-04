"""Mock primary and secondary model providers for testing the router."""

import asyncio

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

primary_app = FastAPI(title="Mock Primary Model")
secondary_app = FastAPI(title="Mock Secondary Model")

# Control flags — set these in tests to simulate failures
primary_return_429 = False
primary_timeout_seconds = 0  # 0 = no delay


@primary_app.post("/v1/chat/completions")
async def primary_completions(request: Request):
    if primary_timeout_seconds > 0:
        await asyncio.sleep(primary_timeout_seconds)

    if primary_return_429:
        return JSONResponse(
            status_code=429,
            content={"error": {"message": "Rate limited by upstream", "type": "rate_limit"}},
        )

    body = await request.json()
    return JSONResponse({
        "model": "primary-model",
        "choices": [{"message": {"role": "assistant", "content": "Response from primary model"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    })


@secondary_app.post("/v1/chat/completions")
async def secondary_completions(request: Request):
    body = await request.json()
    return JSONResponse({
        "model": "secondary-model",
        "choices": [{"message": {"role": "assistant", "content": "Response from secondary model"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 15, "total_tokens": 25},
    })
