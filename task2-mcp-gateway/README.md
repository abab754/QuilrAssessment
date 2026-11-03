# Task 2: MCP Security Gateway Proxy (Tool Filtering & Auth)

## What This Is

A lightweight HTTP/JSON-RPC reverse proxy that sits between an AI agent client and a downstream MCP server. The gateway inspects every incoming request, extracts the caller's role from a Bearer token, and enforces authorization rules before deciding whether to forward the request or reject it.

## Why This Matters

In production MCP deployments, you don't want every client to have unrestricted access to every tool. Some tools (like `admin_reset_key` or `admin_delete_user`) should only be callable by privileged users. This gateway acts as a security layer that enforces those rules without modifying the downstream MCP server itself.

## Architecture

```
AI Agent Client  ──Bearer token + JSON-RPC──>  Gateway Proxy (port 8000)  ──Forward──>  Mock MCP Server (port 8001)
```

The gateway makes three decisions based on the request:

| Request Method | Tool Name | Role | Action |
|----------------|-----------|------|--------|
| `tools/list` | n/a | any | Forward transparently |
| `tools/call` | `search_docs` (no `admin_` prefix) | any | Forward to downstream |
| `tools/call` | `admin_reset_key` (`admin_` prefix) | viewer | Block with `-32001 Unauthorized Tool Call` |

## Files

- **`gateway.py`** — The proxy server. Parses Bearer tokens, inspects JSON-RPC bodies, forwards or blocks.
- **`mock_downstream.py`** — A simple mock MCP server that returns dummy responses for testing.
- **`test_gateway.py`** — 12 tests covering JSON-RPC parsing, proxy forwarding, and authorization logic.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Start the mock downstream server and the gateway in separate terminals:

```bash
# Terminal 1: mock MCP server
uvicorn mock_downstream:app --port 8001

# Terminal 2: gateway proxy
uvicorn gateway:app --port 8000
```

Then test with curl:

```bash
# Token: {"role": "admin"} base64-encoded
ADMIN_TOKEN="eyJyb2xlIjoiYWRtaW4ifQ=="

# Token: {"role": "viewer"} base64-encoded
VIEWER_TOKEN="eyJyb2xlIjoidmlld2VyIn0="

# List tools (any role) — forwarded
curl -X POST http://localhost:8000 \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# Call a normal tool as viewer — forwarded
curl -X POST http://localhost:8000 \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"search_docs","arguments":{}}}'

# Call an admin tool as viewer — blocked with -32001
curl -X POST http://localhost:8000 \
  -H "Authorization: Bearer $VIEWER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"admin_reset_key","arguments":{}}}'
```

## Test

```bash
pytest test_gateway.py -v
```

Tests cover:
- **JSON-RPC parsing** — invalid JSON, missing version, missing method
- **Proxy forwarding** — tools/list and normal tools forwarded correctly for all roles
- **Authorization** — admin tools allowed for admin, blocked for viewer, missing/malformed tokens rejected, request IDs preserved in error responses
