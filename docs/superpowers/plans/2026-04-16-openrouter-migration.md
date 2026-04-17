# OpenRouter Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Point the Letta agent at OpenRouter instead of Anthropic, with model selectable via env var.

**Architecture:** Letta supports OpenAI-compatible endpoints via `model_endpoint_type: "openai"`. OpenRouter exposes exactly this interface. The only code change is in `create_new()` — everything else (tools, memory blocks, sessions, strategy gate) is untouched.

**Tech Stack:** Python, Letta, OpenRouter API (OpenAI-compatible), Docker Compose

---

### Task 1: Update `create_new()` to use OpenRouter

**Files:**
- Modify: `scheduler/agent.py` (the `create_new` classmethod, lines 281–320)
- Modify: `tests/conftest.py` (swap env var name)
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write a failing test for the new llm_config**

Add this test to `tests/test_agent.py`:

```python
def test_create_new_uses_openrouter_config(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-4o")
    monkeypatch.setenv("OPENROUTER_CONTEXT_WINDOW", "128000")

    with patch("scheduler.agent.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        created_agent = MagicMock()
        created_agent.id = "new-agent-id"
        mock_client.create_agent.return_value = created_agent

        LettaTraderAgent.create_new("test_trader", server_url="http://localhost:8283")

        call_kwargs = mock_client.create_agent.call_args[1]
        llm = call_kwargs["llm_config"]
        assert llm["model_endpoint_type"] == "openai"
        assert llm["model_endpoint"] == "https://openrouter.ai/api/v1"
        assert llm["model"] == "openai/gpt-4o"
        assert llm["context_window"] == 128000
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
pytest tests/test_agent.py::test_create_new_uses_openrouter_config -v
```

Expected: FAIL — `AssertionError` because current config has `model_endpoint_type: "anthropic"`.

- [ ] **Step 3: Update `create_new()` in `scheduler/agent.py`**

Replace the `llm_config` dict inside `create_new()` (currently around line 311):

```python
        agent = client.create_agent(
            name=agent_name,
            llm_config={
                "model": os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
                "model_endpoint_type": "openai",
                "model_endpoint": "https://openrouter.ai/api/v1",
                "context_window": int(os.environ.get("OPENROUTER_CONTEXT_WINDOW", "200000")),
            },
            memory=memory,
            tool_exec_environment_variables=tool_env,
        )
```

- [ ] **Step 4: Run the test to confirm it passes**

```bash
pytest tests/test_agent.py::test_create_new_uses_openrouter_config -v
```

Expected: PASS

- [ ] **Step 5: Run the full agent test suite**

```bash
pytest tests/test_agent.py -v
```

Expected: All tests PASS (the other tests don't touch `create_new`, so they are unaffected).

- [ ] **Step 6: Commit**

```bash
git add scheduler/agent.py tests/test_agent.py
git commit -m "feat: switch Letta agent to OpenRouter (openai-compatible endpoint)"
```

---

### Task 2: Update Docker Compose and env example

**Files:**
- Modify: `docker-compose.yml` (Letta service `environment` block)
- Modify: `.env.example`
- Modify: `tests/conftest.py` (rename dummy env var)

- [ ] **Step 1: Update `docker-compose.yml` Letta service environment**

In `docker-compose.yml`, find the `letta:` service `environment:` block and replace:

```yaml
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
```

with:

```yaml
    environment:
      - OPENAI_API_KEY=${OPENROUTER_API_KEY}
```

(Letta reads `OPENAI_API_KEY` when `model_endpoint_type` is `"openai"`.)

- [ ] **Step 2: Update `.env.example`**

Replace the last line:

```
# Claude model for Letta agent
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

with:

```
# OpenRouter (model routing layer — replaces direct Anthropic API)
OPENROUTER_API_KEY=your_openrouter_api_key_here
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
# Optional: override context window size (default 200000)
# OPENROUTER_CONTEXT_WINDOW=200000
```

- [ ] **Step 3: Update `tests/conftest.py`**

Replace:

```python
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_anthropic_key")
```

with:

```python
    monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter_key")
    monkeypatch.setenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
```

- [ ] **Step 4: Run full test suite**

```bash
pytest -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example tests/conftest.py
git commit -m "feat: wire OpenRouter API key into docker-compose and update env example"
```

---

### Task 3: Deploy to VPS and reset

This task is manual — run these commands on the VPS after pushing the branch.

- [ ] **Step 1: Push to remote**

```bash
git push
```

- [ ] **Step 2: SSH to VPS and pull**

```bash
ssh vps "cd ~/ClaudeTrading && git pull"
```

- [ ] **Step 3: Stop the stack**

```bash
ssh vps "cd ~/ClaudeTrading && docker-compose down"
```

- [ ] **Step 4: Wipe Letta and agent-state volumes**

```bash
ssh vps "docker volume rm claudetrading_letta-db claudetrading_agent-state"
```

- [ ] **Step 5: Add OpenRouter env vars to `.env` on VPS**

```bash
ssh vps "cd ~/ClaudeTrading && nano .env"
```

Remove `ANTHROPIC_API_KEY`. Add:

```
OPENROUTER_API_KEY=<your real OpenRouter key>
OPENROUTER_MODEL=anthropic/claude-3.5-sonnet
```

- [ ] **Step 6: Bring the stack back up**

```bash
ssh vps "cd ~/ClaudeTrading && docker-compose up -d"
```

Wait ~90s for Letta to pass its healthcheck.

- [ ] **Step 7: Run bootstrap**

```bash
ssh vps "cd ~/ClaudeTrading && docker-compose exec scheduler python -m scheduler.bootstrap"
```

Expected output ends with: `Bootstrap complete. Agent ID saved to /app/state/.agent_id`

- [ ] **Step 8: Tail scheduler logs to verify first session fires cleanly**

```bash
ssh vps "cd ~/ClaudeTrading && docker-compose logs -f scheduler"
```

No errors about missing API keys or unknown model.

- [ ] **Step 9: Reset Alpaca paper account**

In a browser: Alpaca dashboard → Paper Account → Settings → **Reset Paper Account**.
Confirms balance returns to $50,000.
