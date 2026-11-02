# Task 3: LLM Gateway Streaming Guardrail (PII Redaction)

An LLM Gateway proxy that streams responses from an LLM provider while redacting PII (emails, SSNs, credit card numbers) in real time.

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

- Real-time PII detection and redaction in streaming responses
- Handles partial pattern matches across chunk boundaries
- Minimal TTFT impact with efficient buffering strategy
- Redacts emails, SSNs, and credit card numbers
