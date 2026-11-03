import base64
import json
import logging
import sys

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

DOWNSTREAM_URL = "http://localhost:8001"

app = FastAPI(title="MCP Security Gateway")
security = HTTPBearer()


def extract_role(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """Decode the Bearer token (base64-encoded JSON) and return the role."""
    try:
        decoded = base64.b64decode(credentials.credentials).decode("utf-8")
        data = json.loads(decoded)
        role = data.get("role")
        if role is None:
            raise ValueError("Missing role")
        return role
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Invalid or malformed Bearer token")


def make_jsonrpc_error(request_id, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


@app.post("/")
async def gateway(request: Request, role: str = Depends(extract_role)):
    """Main gateway endpoint — inspects JSON-RPC and decides forward vs block."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(make_jsonrpc_error(None, -32700, "Parse error"))

    # Validate basic JSON-RPC structure
    if body.get("jsonrpc") != "2.0" or "method" not in body:
        return JSONResponse(
            make_jsonrpc_error(body.get("id"), -32600, "Invalid Request")
        )

    request_id = body.get("id")
    method = body["method"]

    logger.info("Received method=%s from role=%s", method, role)

    # tools/list — forward transparently
    if method == "tools/list":
        return await forward_to_downstream(body)

    # tools/call — check authorization for admin_ tools
    if method == "tools/call":
        tool_name = body.get("params", {}).get("name", "")

        if tool_name.startswith("admin_") and role != "admin":
            logger.warning(
                "Blocked %s for role=%s on tool=%s", method, role, tool_name
            )
            return JSONResponse(
                make_jsonrpc_error(request_id, -32001, "Unauthorized Tool Call")
            )

        return await forward_to_downstream(body)

    # Any other method — forward transparently
    return await forward_to_downstream(body)


async def forward_to_downstream(body: dict) -> JSONResponse:
    """Forward the JSON-RPC request to the downstream MCP server."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                DOWNSTREAM_URL, json=body, timeout=10.0
            )
            return JSONResponse(
                content=response.json(), status_code=response.status_code
            )
        except httpx.ConnectError:
            return JSONResponse(
                make_jsonrpc_error(
                    body.get("id"), -32603, "Downstream server unavailable"
                )
            )
