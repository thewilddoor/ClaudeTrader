# OpenRouter Migration Design

**Date:** 2026-04-16
**Status:** Approved

## Goal

Switch the Letta agent's LLM backend from Anthropic (direct) to OpenRouter. Keep all other
infrastructure identical — Letta, tool registration, memory blocks, strategy gate, sessions,
notifier, and SQLite schema are untouched.

Also perform a full clean reset: wipe Letta agent memory and reset the Alpaca paper trading
account to $50k.

## What Changes

### 1. `scheduler/agent.py` — `create_new()`

Replace the hardcoded Anthropic `llm_config` with an OpenRouter config:

```python
llm_config={
    "model": os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
    "model_endpoint_type": "openai",
    "model_endpoint": "https://openrouter.ai/api/v1",
    "context_window": int(os.environ.get("OPENROUTER_CONTEXT_WINDOW", "200000")),
},
```

- `model_endpoint_type: "openai"` tells Letta to use the OpenAI-compatible API surface.
- Letta reads the API key from its `OPENAI_API_KEY` environment variable for this endpoint type.
- Model slug and context window are env-var driven for easy switching without code changes.

### 2. `docker-compose.yml` — Letta service

Replace `ANTHROPIC_API_KEY` with `OPENAI_API_KEY` (Letta's env var for openai-type endpoints):

```yaml
environment:
  - OPENAI_API_KEY=${OPENROUTER_API_KEY}
```

### 3. `.env.example`

Replace `ANTHROPIC_API_KEY` with:

```
OPENROUTER_API_KEY=your_openrouter_key_here
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
```

`OPENROUTER_CONTEXT_WINDOW` is optional (defaults to 200000).

## What Does NOT Change

- Letta service itself (same Docker image)
- All 11 registered tools (FMP, Alpaca, Serper, PyExec, SQLite)
- Memory block structure and initial content
- Strategy gate logic
- Sessions and prompts
- Notifier / Telegram alerts
- SQLite schema

## Deploy Sequence

Run on the VPS after pushing the code changes:

```bash
docker-compose down
docker volume rm claudetrading_letta-db    # wipes Letta agent + memory
docker volume rm claudetrading_agent-state # wipes .agent_id so bootstrap creates fresh agent
# update .env: add OPENROUTER_API_KEY, OPENROUTER_MODEL; remove ANTHROPIC_API_KEY
docker-compose up -d
docker-compose exec scheduler python -m scheduler.bootstrap
```

Also reset the Alpaca paper account manually:
- Alpaca dashboard → Paper Account → Settings → "Reset Paper Account" ($50k)

## Model Selection

Model is configured via `OPENROUTER_MODEL` env var. Default: `anthropic/claude-3.5-sonnet`.
Change it in `.env` on the VPS at any time; takes effect on next bootstrap (agent recreate).
No code change needed to switch models.
