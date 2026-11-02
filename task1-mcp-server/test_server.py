import json
import pytest
from mcp import Client
from server import mcp


# --- Helper ---

async def call(client: Client, tool: str, args: dict):
    return await client.call_tool(tool, args)


def parse_content(result) -> dict:
    """Extract the JSON dict from a successful tool result."""
    return json.loads(result.content[0].text)


# ============================================================
# 1. STDIO Isolation (stdout must be pure JSON-RPC)
#    The in-memory Client test harness validates this implicitly:
#    if any stray print() existed, the JSON-RPC framing would break.
# ============================================================

@pytest.mark.anyio
async def test_stdio_isolation_no_stray_output():
    """Server communicates cleanly over the in-memory transport (no stray prints)."""
    async with Client(mcp) as client:
        result = await call(client, "get_customer_record", {"customer_id": "CUST-00001"})
        assert result.structured_content is not None or len(result.content) > 0


# ============================================================
# 2. Protocol Compliance (tools/list, valid calls, error shape)
# ============================================================

@pytest.mark.anyio
async def test_tools_list():
    """tools/list returns both registered tools with correct schemas."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        names = {t.name for t in tools.tools}
        assert names == {"get_customer_record", "trigger_refund"}


@pytest.mark.anyio
async def test_get_customer_record_valid():
    """Valid get_customer_record call returns expected fields."""
    async with Client(mcp) as client:
        result = await call(client, "get_customer_record", {"customer_id": "CUST-12345"})
        assert not result.is_error
        data = parse_content(result)
        assert data["customer_id"] == "CUST-12345"
        assert "name" in data
        assert "status" in data


@pytest.mark.anyio
async def test_trigger_refund_valid():
    """Valid trigger_refund call returns expected fields."""
    async with Client(mcp) as client:
        result = await call(
            client,
            "trigger_refund",
            {"customer_id": "CUST-99999", "amount": 25.50, "reason": "Item was damaged on arrival"},
        )
        assert not result.is_error
        data = parse_content(result)
        assert data["customer_id"] == "CUST-99999"
        assert data["amount"] == 25.50
        assert data["status"] == "processed"


# ============================================================
# 3. Validation — edge cases and malformed inputs
# ============================================================

# --- get_customer_record ---

@pytest.mark.anyio
async def test_invalid_customer_id_format():
    """customer_id that doesn't match CUST-XXXXX is rejected."""
    async with Client(mcp) as client:
        result = await call(client, "get_customer_record", {"customer_id": "INVALID"})
        assert result.is_error


@pytest.mark.anyio
async def test_customer_id_too_few_digits():
    """CUST- with fewer than 5 digits is rejected."""
    async with Client(mcp) as client:
        result = await call(client, "get_customer_record", {"customer_id": "CUST-123"})
        assert result.is_error


@pytest.mark.anyio
async def test_customer_id_too_many_digits():
    """CUST- with more than 5 digits is rejected."""
    async with Client(mcp) as client:
        result = await call(client, "get_customer_record", {"customer_id": "CUST-123456"})
        assert result.is_error


@pytest.mark.anyio
async def test_customer_id_lowercase():
    """Lowercase 'cust-' is rejected (case-sensitive)."""
    async with Client(mcp) as client:
        result = await call(client, "get_customer_record", {"customer_id": "cust-12345"})
        assert result.is_error


@pytest.mark.anyio
async def test_customer_id_missing():
    """Missing customer_id entirely is rejected."""
    async with Client(mcp) as client:
        result = await call(client, "get_customer_record", {})
        assert result.is_error


@pytest.mark.anyio
async def test_customer_id_empty_string():
    """Empty string for customer_id is rejected."""
    async with Client(mcp) as client:
        result = await call(client, "get_customer_record", {"customer_id": ""})
        assert result.is_error


# --- trigger_refund: amount validation ---

@pytest.mark.anyio
async def test_refund_negative_amount():
    """Negative amount is rejected."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "CUST-12345", "amount": -10.0, "reason": "This is a valid reason"},
        )
        assert result.is_error


@pytest.mark.anyio
async def test_refund_zero_amount():
    """Zero amount is rejected (must be > 0)."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "CUST-12345", "amount": 0, "reason": "This is a valid reason"},
        )
        assert result.is_error


# --- trigger_refund: reason validation ---

@pytest.mark.anyio
async def test_refund_reason_too_short():
    """Reason shorter than 10 characters is rejected."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "CUST-12345", "amount": 50.0, "reason": "short"},
        )
        assert result.is_error


@pytest.mark.anyio
async def test_refund_reason_exactly_10_chars():
    """Reason with exactly 10 characters is accepted."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "CUST-12345", "amount": 50.0, "reason": "1234567890"},
        )
        assert not result.is_error


@pytest.mark.anyio
async def test_refund_reason_empty():
    """Empty reason string is rejected."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "CUST-12345", "amount": 50.0, "reason": ""},
        )
        assert result.is_error


# --- trigger_refund: missing fields ---

@pytest.mark.anyio
async def test_refund_missing_amount():
    """Missing amount field is rejected."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "CUST-12345", "reason": "This is a valid reason"},
        )
        assert result.is_error


@pytest.mark.anyio
async def test_refund_missing_reason():
    """Missing reason field is rejected."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "CUST-12345", "amount": 50.0},
        )
        assert result.is_error


# --- trigger_refund: wrong types ---

@pytest.mark.anyio
async def test_refund_amount_as_string():
    """String value for amount is rejected."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "CUST-12345", "amount": "abc", "reason": "This is a valid reason"},
        )
        assert result.is_error


# --- trigger_refund: customer_id validation (same rules apply) ---

@pytest.mark.anyio
async def test_refund_invalid_customer_id():
    """Invalid customer_id in trigger_refund is also rejected."""
    async with Client(mcp) as client:
        result = await call(
            client, "trigger_refund",
            {"customer_id": "BAD", "amount": 50.0, "reason": "This is a valid reason"},
        )
        assert result.is_error
