import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient

from gateway import app, DOWNSTREAM_URL
from mock_downstream import app as downstream_app


def make_token(role: str) -> str:
    """Create a base64-encoded Bearer token with the given role."""
    return base64.b64encode(json.dumps({"role": role}).encode()).decode()


ADMIN_TOKEN = make_token("admin")
VIEWER_TOKEN = make_token("viewer")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def mock_downstream(monkeypatch):
    """Patch gateway to forward to an in-process mock instead of a real server."""
    import httpx as _httpx
    from gateway import forward_to_downstream
    from fastapi.responses import JSONResponse

    original_forward = forward_to_downstream

    async def patched_forward(body: dict) -> JSONResponse:
        transport = ASGITransport(app=downstream_app)
        async with _httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/", json=body)
            return JSONResponse(content=response.json(), status_code=response.status_code)

    monkeypatch.setattr("gateway.forward_to_downstream", patched_forward)


# ============================================================
# 1. JSON-RPC Parsing
# ============================================================

@pytest.mark.anyio
async def test_invalid_json_body():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            content=b"not json",
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}", "Content-Type": "application/json"},
        )
        data = response.json()
        assert data["error"]["code"] == -32700


@pytest.mark.anyio
async def test_missing_jsonrpc_version(mock_downstream):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={"method": "tools/list", "id": 1},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        data = response.json()
        assert data["error"]["code"] == -32600


@pytest.mark.anyio
async def test_missing_method(mock_downstream):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 1},
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        data = response.json()
        assert data["error"]["code"] == -32600


# ============================================================
# 2. Proxy Forwarding
# ============================================================

@pytest.mark.anyio
async def test_tools_list_forwarded(mock_downstream):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"},
        )
        data = response.json()
        assert "result" in data
        assert "tools" in data["result"]


@pytest.mark.anyio
async def test_normal_tool_forwarded_for_viewer(mock_downstream):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "search_docs", "arguments": {}},
            },
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"},
        )
        data = response.json()
        assert "result" in data
        assert "Mock result from search_docs" in data["result"]["content"][0]["text"]


@pytest.mark.anyio
async def test_normal_tool_forwarded_for_admin(mock_downstream):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "get_status", "arguments": {}},
            },
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        data = response.json()
        assert "result" in data


# ============================================================
# 3. Authorization Logic
# ============================================================

@pytest.mark.anyio
async def test_admin_tool_allowed_for_admin(mock_downstream):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={
                "jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "admin_reset_key", "arguments": {}},
            },
            headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        )
        data = response.json()
        assert "result" in data
        assert "error" not in data


@pytest.mark.anyio
async def test_admin_tool_blocked_for_viewer(mock_downstream):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={
                "jsonrpc": "2.0", "id": 5, "method": "tools/call",
                "params": {"name": "admin_reset_key", "arguments": {}},
            },
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"},
        )
        data = response.json()
        assert data["error"]["code"] == -32001
        assert "Unauthorized" in data["error"]["message"]


@pytest.mark.anyio
async def test_another_admin_tool_blocked_for_viewer(mock_downstream):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={
                "jsonrpc": "2.0", "id": 6, "method": "tools/call",
                "params": {"name": "admin_delete_user", "arguments": {}},
            },
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"},
        )
        data = response.json()
        assert data["error"]["code"] == -32001


@pytest.mark.anyio
async def test_missing_auth_header():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 7, "method": "tools/list", "params": {}},
        )
        assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_malformed_token():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {}},
            headers={"Authorization": "Bearer not-valid-base64!!!"},
        )
        # Should get an HTTP error for bad token
        assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_request_id_preserved_in_error():
    """The error response must echo back the same id from the request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/",
            json={
                "jsonrpc": "2.0", "id": 42, "method": "tools/call",
                "params": {"name": "admin_reset_key", "arguments": {}},
            },
            headers={"Authorization": f"Bearer {VIEWER_TOKEN}"},
        )
        data = response.json()
        assert data["id"] == 42
        assert data["error"]["code"] == -32001
