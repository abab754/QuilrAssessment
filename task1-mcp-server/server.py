from mcp.server import MCPServer
from typing import Annotated
from pydantic import Field
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

mcp = MCPServer("Task1")


@mcp.tool()
def get_customer_record(
    customer_id: Annotated[str, Field(pattern=r"^CUST-\d{5}$", description="Customer ID in CUST-XXXXX format")],
) -> dict:
    """Get a customer's record given the customer's id."""
    logger.info("Fetching record for %s", customer_id)
    return {
        "customer_id": customer_id,
        "name": "John Doe",
        "email": "john.doe@gmail.com",
        "status": "active",
    }


@mcp.tool()
def trigger_refund(
    customer_id: Annotated[str, Field(pattern=r"^CUST-\d{5}$", description="Customer ID in CUST-XXXXX format")],
    amount: Annotated[float, Field(gt=0, description="Refund amount (must be positive)")],
    reason: Annotated[str, Field(min_length=10, description="Reason for refund (at least 10 characters)")],
) -> dict:
    """Trigger a refund for a customer."""
    logger.info("Processing refund of %.2f for %s", amount, customer_id)
    return {
        "refund_id": "REF-12345",
        "customer_id": customer_id,
        "amount": amount,
        "reason": reason,
        "status": "processed",
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")