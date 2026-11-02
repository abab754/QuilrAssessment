# Task 4: Rate-Limiting & Model Fallback Router for LLM Gateways

A resilient model-routing module with token-aware sliding window rate limiting and automatic failover to backup model providers.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
python router.py
```

## Key Features

- Token-aware sliding window rate limiter (50k tokens/min per tenant)
- Automatic failover on 429 or 3000ms timeout
- SQLite-backed rate limit state
- Standardized error responses (no leaked internals)
