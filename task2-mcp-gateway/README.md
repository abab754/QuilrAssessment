# Task 2: MCP Security Gateway Proxy (Tool Filtering & Auth)

A lightweight HTTP/JSON-RPC reverse proxy that sits between an AI agent client and a downstream MCP server, enforcing role-based authorization on tool calls.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python gateway.py
```

## Key Features

- Bearer token authentication with role extraction
- Transparent forwarding for `tools/list` requests
- Role-based access control on `tools/call` (admin_ prefix tools require admin role)
- JSON-RPC error responses for unauthorized access
