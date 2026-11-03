"""Mock downstream MCP server that returns dummy responses for testing the gateway."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="Mock MCP Downstream Server")

MOCK_TOOLS = [
    {"name": "search_docs", "description": "Search documents"},
    {"name": "admin_reset_key", "description": "Reset an API key (admin only)"},
    {"name": "admin_delete_user", "description": "Delete a user (admin only)"},
    {"name": "get_status", "description": "Get system status"},
]


@app.post("/")
async def handle_rpc(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        )

    request_id = body.get("id")
    method = body.get("method")

    if method == "tools/list":
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "result": {"tools": MOCK_TOOLS}}
        )

    if method == "tools/call":
        tool_name = body.get("params", {}).get("name", "unknown")
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": f"Mock result from {tool_name}"}]
                },
            }
        )

    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    )
