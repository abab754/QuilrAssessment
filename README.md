# Quilr Assessment

A collection of four backend/infrastructure projects demonstrating proficiency in MCP servers, API gateways, streaming proxies, and resilient routing.

## Projects

| Task | Description | Tech |
|------|-------------|------|
| [Task 1](./task1-mcp-server/) | Custom MCP Server with strict validation & stdio transport | Python, MCP SDK, Pydantic |
| [Task 2](./task2-mcp-gateway/) | MCP Security Gateway Proxy with tool filtering & auth | Python |
| [Task 3](./task3-llm-streaming-guardrail/) | LLM Gateway Streaming Guardrail with PII redaction | Python |
| [Task 4](./task4-rate-limiter-router/) | Rate-Limiting & Model Fallback Router for LLM Gateways | Python, SQLite |

## Setup

Each task is self-contained with its own virtual environment and dependencies.

```bash
cd task1-mcp-server   # (or any task directory)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

See each task's README for specific run and test instructions.
