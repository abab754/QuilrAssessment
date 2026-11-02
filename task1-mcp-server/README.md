# Task 1: Custom MCP Server with Strict Validation & Transport Handling

An MCP server exposing two tools (`get_customer_record` and `trigger_refund`) with strict input validation via Pydantic, communicating over stdio transport.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python server.py
```

## Key Features

- Strict input schema validation using Pydantic
- CUST-XXXXX format enforcement for customer IDs
- stdio transport with stdout reserved for JSON-RPC only (logs go to stderr)
- Standard MCP JSON-RPC error codes for invalid inputs
