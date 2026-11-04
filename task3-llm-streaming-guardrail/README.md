# Task 3: LLM Gateway Streaming Guardrail (PII Redaction)

## What This Is

An LLM Gateway proxy that sits between a client and an LLM provider. It forwards chat completion requests upstream, intercepts the streaming response chunks in real time, and redacts any PII (emails, SSNs, credit card numbers) before the data reaches the client.

## Why This Matters

LLMs can accidentally leak sensitive data — from training data, echoed user input, or hallucinated personal information. In regulated environments (healthcare, finance, legal), PII must never reach the client unfiltered. This proxy catches it in real time during streaming, without waiting for the full response or accumulating it in memory.

## Architecture

```
Client  ──POST /v1/chat/completions──>  Gateway (port 8000)  ──Forward──>  LLM Provider (port 8001)
                                             │
                                        PIIRedactor
                                        (buffer + regex)
                                             │
Client  <──SSE chunks with PII redacted──  Gateway
```

The hard part: LLMs stream text in small chunks, and a PII pattern like `123-45-6789` can arrive split across two chunks (`"123-45"` then `"-6789"`). The `PIIRedactor` maintains a buffer of trailing characters that could be part of an incomplete pattern, flushes safe text immediately, and only holds back what might complete a match on the next chunk.

## Files

- **`gateway.py`** — The proxy server with `PIIRedactor` class and SSE stream processing.
- **`mock_llm.py`** — Mock LLM provider that streams SSE responses with intentionally split PII for testing.
- **`test_gateway.py`** — 14 tests covering unit redaction and full integration pipeline.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Start the mock LLM and the gateway in separate terminals:

```bash
# Terminal 1: mock LLM provider
uvicorn mock_llm:app --port 8001

# Terminal 2: gateway proxy
uvicorn gateway:app --port 8000
```

Then test with curl (the mock LLM picks a scenario based on the message content):

```bash
# Clean response — no PII
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"clean"}],"stream":true}'

# Email PII (split across chunks) — should be redacted
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"email"}],"stream":true}'

# SSN PII (split across chunks) — should be redacted
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"ssn"}],"stream":true}'

# Mixed PII (email + SSN + credit card) — all redacted
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"mixed"}],"stream":true}'
```

## Test

```bash
pytest test_gateway.py -v
```

Tests cover:
- **Unit: single-chunk PII** — email, SSN, credit card each redacted in one pass
- **Unit: cross-chunk PII** — each PII type split across two chunks and still caught
- **Unit: edge cases** — clean text passes through, multiple PII in one chunk, empty flush
- **Integration: full pipeline** — mock LLM streams through the redactor for all 5 scenarios (clean, email, SSN, credit card, mixed)
