import logging
import sys
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import rate_limiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

PRIMARY_URL = "http://localhost:8001"
SECONDARY_URL = "http://localhost:8002"
PRIMARY_TIMEOUT = 3.0  # seconds


@asynccontextmanager
async def lifespan(app):
    await rate_limiter.init_db()
    yield

app = FastAPI(title="LLM Gateway Router", lifespan=lifespan)


def gateway_error(status_code: int, message: str) -> JSONResponse:
    """Return a standardized gateway error — never leak upstream details."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "type": "gateway_error",
                "message": message,
            }
        },
    )


def extract_api_key(request: Request) -> str | None:
    """Extract the tenant API key from the Authorization header."""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def estimate_tokens(body: dict) -> int:
    """Estimate token count from the request. Uses a rough heuristic on message length."""
    total_chars = 0
    for msg in body.get("messages", []):
        total_chars += len(msg.get("content", ""))
    # Rough estimate: ~4 chars per token
    return max(total_chars // 4, 1)


async def call_provider(url: str, body: dict, timeout: float) -> httpx.Response:
    """Make a request to a model provider with a timeout."""
    async with httpx.AsyncClient() as client:
        return await client.post(
            f"{url}/v1/chat/completions",
            json=body,
            timeout=timeout,
        )


@app.post("/v1/chat/completions")
async def route_completion(request: Request):
    # Extract API key
    api_key = extract_api_key(request)
    if not api_key:
        return gateway_error(401, "Missing or invalid API key")

    # Parse body
    try:
        body = await request.json()
    except Exception:
        return gateway_error(400, "Invalid request body")

    # Rate limit check
    estimated_tokens = estimate_tokens(body)
    allowed, remaining = await rate_limiter.check_rate_limit(api_key, estimated_tokens)
    if not allowed:
        logger.warning("Rate limit exceeded for %s (remaining: %d)", api_key, remaining)
        return gateway_error(429, "Rate limit exceeded. Try again later.")

    # Try primary model
    try:
        response = await call_provider(PRIMARY_URL, body, timeout=PRIMARY_TIMEOUT)

        if response.status_code == 429:
            logger.warning("Primary returned 429, falling back to secondary")
        else:
            return JSONResponse(content=response.json(), status_code=response.status_code)

    except httpx.TimeoutException:
        logger.warning("Primary timed out after %.1fs, falling back to secondary", PRIMARY_TIMEOUT)
    except httpx.ConnectError:
        logger.warning("Primary unavailable, falling back to secondary")
    except Exception:
        logger.exception("Unexpected error calling primary")

    # Fallback to secondary model
    try:
        response = await call_provider(SECONDARY_URL, body, timeout=10.0)
        return JSONResponse(content=response.json(), status_code=response.status_code)
    except Exception:
        logger.exception("Secondary model also failed")
        return gateway_error(502, "All model providers are currently unavailable")
