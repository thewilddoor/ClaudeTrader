# Phase 1: Letta Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Letta with a direct Anthropic SDK call loop, add SQLite-backed MemoryStore (5 memory blocks + session log), add Haiku-powered session digests, and cut token costs from ~$10/day to ~$0.50/day.

**Architecture:** Each session is a stateless API call. Static system prompt (~8,500 tokens) is cached by Anthropic after the first call. Five memory blocks are read from SQLite at session start and injected fresh each call. Haiku runs post-session in a background thread to summarize the session into a structured digest that feeds into the next session's context.

**Tech Stack:** `anthropic` Python SDK, SQLite (existing `trades.db`), APScheduler (existing), Haiku 4.5 for digests.

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `scheduler/memory.py` | MemoryStore: read/write memory blocks + session log from SQLite |
| Create | `scheduler/digester.py` | SessionDigester: Haiku-powered session summarization |
| Rewrite | `scheduler/agent.py` | AgentCore: tool loop, prompt caching, memory interface |
| Rewrite | `scheduler/sessions.py` | Session prompt builders + build_recent_context helper |
| Modify | `scheduler/bootstrap.py` | Remove Letta creation; call bootstrap_memory_table |
| Modify | `scheduler/main.py` | Use AgentCore; build recent_context per session |
| Modify | `scheduler/tools/sqlite.py` | Add bootstrap_memory_table() + memory/session_log schemas |
| Modify | `scheduler/tools/fmp.py` | Change fmp_ohlcv default limit 90 → 20 |
| Modify | `requirements.txt` | Add `anthropic>=0.50.0`; remove `letta` |
| Modify | `docker-compose.yml` | Remove letta service and letta-db volume |
| Modify | `tests/conftest.py` | Add ANTHROPIC_API_KEY; remove Letta env vars |
| Delete | `scheduler/tools/registry.py` | No longer needed |
| Rewrite | `tests/test_agent.py` | Tests for AgentCore |
| Create | `tests/test_memory.py` | Tests for MemoryStore |
| Create | `tests/test_digester.py` | Tests for SessionDigester |

**Untouched:** `scheduler/strategy_gate.py`, `scheduler/notifier.py`, `scheduler/tools/alpaca.py`, `scheduler/tools/serper.py`, `scheduler/tools/pyexec.py`, all indicator scripts.

---

## Task 1: Database Schema Extensions

Add `memory` and `session_log` tables to the existing SQLite schema in `sqlite.py`. These tables live in the same `trades.db` that already holds trades and strategy versions.

**Files:**
- Modify: `scheduler/tools/sqlite.py`
- Create: `tests/test_memory_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_schema.py
import sqlite3
import tempfile
import os
import pytest


def test_bootstrap_creates_memory_table():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        import scheduler.tools.sqlite as sqlite_mod
        original = sqlite_mod.DB_PATH
        sqlite_mod.DB_PATH = db_path
        sqlite_mod.bootstrap_db()
        sqlite_mod.bootstrap_memory_table({"strategy_doc": "initial"})
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM memory WHERE key='strategy_doc'").fetchone()
        assert row is not None
        assert row[0] == "initial"
        conn.close()
    finally:
        sqlite_mod.DB_PATH = original
        os.unlink(db_path)


def test_bootstrap_memory_table_is_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        import scheduler.tools.sqlite as sqlite_mod
        original = sqlite_mod.DB_PATH
        sqlite_mod.DB_PATH = db_path
        sqlite_mod.bootstrap_db()
        sqlite_mod.bootstrap_memory_table({"key1": "val1"})
        sqlite_mod.bootstrap_memory_table({"key1": "SHOULD_NOT_OVERWRITE"})
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM memory WHERE key='key1'").fetchone()
        assert row[0] == "val1"   # original preserved — idempotent
        conn.close()
    finally:
        sqlite_mod.DB_PATH = original
        os.unlink(db_path)


def test_bootstrap_creates_session_log_table():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        import scheduler.tools.sqlite as sqlite_mod
        original = sqlite_mod.DB_PATH
        sqlite_mod.DB_PATH = db_path
        sqlite_mod.bootstrap_db()
        sqlite_mod.bootstrap_memory_table({})
        conn = sqlite3.connect(db_path)
        # Should not raise
        conn.execute("SELECT id, session_name, date, raw_response, digest FROM session_log LIMIT 1")
        conn.close()
    finally:
        sqlite_mod.DB_PATH = original
        os.unlink(db_path)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/ziyao_bai/Desktop/ClaudeTrading
source .venv/bin/activate
pytest tests/test_memory_schema.py -v
```
Expected: `FAILED` — `bootstrap_memory_table` not yet defined.

- [ ] **Step 3: Add schema constants and bootstrap_memory_table to sqlite.py**

Open `scheduler/tools/sqlite.py`. After the existing `_SCHEMA` constant, add:

```python
_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT NOT NULL,
    date         TEXT NOT NULL,
    raw_response TEXT NOT NULL,
    digest       TEXT,
    logged_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def bootstrap_memory_table(initial_values: dict) -> None:
    """Create memory and session_log tables. Insert initial_values without overwriting existing rows.

    Idempotent — safe to call on every startup.
    """
    conn = _connect()
    try:
        conn.executescript(_MEMORY_SCHEMA)
        for key, value in initial_values.items():
            conn.execute(
                "INSERT INTO memory (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (key, value),
            )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_memory_schema.py -v
```
Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_memory_schema.py
git commit -m "feat: add memory and session_log tables to SQLite schema"
```

---

## Task 2: MemoryStore Class

A clean Python class that wraps all SQLite reads/writes for memory blocks and session logs. `AgentCore` and `strategy_gate.py` both use this interface.

**Files:**
- Create: `scheduler/memory.py`
- Create: `tests/test_memory.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory.py
import sqlite3
import tempfile
import os
import pytest


@pytest.fixture
def mem_db(monkeypatch):
    """Provide a MemoryStore backed by a temp database with tables created."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    import scheduler.tools.sqlite as sqlite_mod
    original = sqlite_mod.DB_PATH
    sqlite_mod.DB_PATH = db_path
    sqlite_mod.bootstrap_db()
    sqlite_mod.bootstrap_memory_table({"strategy_doc": "initial_doc", "watchlist": "initial_wl"})
    from scheduler.memory import MemoryStore
    store = MemoryStore(db_path=db_path)
    yield store
    sqlite_mod.DB_PATH = original
    os.unlink(db_path)


def test_read_returns_initial_value(mem_db):
    assert mem_db.read("strategy_doc") == "initial_doc"


def test_read_returns_none_for_missing_key(mem_db):
    assert mem_db.read("nonexistent_key") is None


def test_write_updates_existing_key(mem_db):
    mem_db.write("strategy_doc", "updated_doc")
    assert mem_db.read("strategy_doc") == "updated_doc"


def test_write_creates_new_key(mem_db):
    mem_db.write("observations", "first observation")
    assert mem_db.read("observations") == "first observation"


def test_read_all_returns_all_keys(mem_db):
    result = mem_db.read_all()
    assert "strategy_doc" in result
    assert "watchlist" in result
    assert result["strategy_doc"] == "initial_doc"


def test_log_session_and_retrieve(mem_db):
    log_id = mem_db.log_session("pre_market", "2026-04-16", "session response text")
    assert isinstance(log_id, int)
    assert log_id > 0


def test_update_session_digest(mem_db):
    log_id = mem_db.log_session("eod_reflection", "2026-04-16", "response")
    mem_db.update_session_digest(log_id, "DECISIONS MADE:\n- Opened NVDA long")
    digests = mem_db.get_recent_digests(n=2)
    assert len(digests) == 1
    assert "NVDA" in digests[0]["digest"]


def test_get_recent_digests_returns_chronological_order(mem_db):
    id1 = mem_db.log_session("pre_market", "2026-04-16", "r1")
    mem_db.update_session_digest(id1, "digest one")
    id2 = mem_db.log_session("market_open", "2026-04-16", "r2")
    mem_db.update_session_digest(id2, "digest two")
    digests = mem_db.get_recent_digests(n=2)
    assert len(digests) == 2
    assert digests[0]["digest"] == "digest one"   # oldest first
    assert digests[1]["digest"] == "digest two"


def test_get_recent_digests_skips_null_digests(mem_db):
    mem_db.log_session("pre_market", "2026-04-16", "no digest yet")  # no update_session_digest
    id2 = mem_db.log_session("market_open", "2026-04-16", "r2")
    mem_db.update_session_digest(id2, "only digest")
    digests = mem_db.get_recent_digests(n=5)
    assert len(digests) == 1
    assert digests[0]["digest"] == "only digest"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_memory.py -v
```
Expected: `FAILED` — `scheduler.memory` module not found.

- [ ] **Step 3: Create scheduler/memory.py**

```python
# scheduler/memory.py
"""SQLite-backed persistent memory store for trading agent.

Manages five memory blocks (strategy_doc, watchlist, performance_snapshot,
today_context, observations) and a session log with Haiku-generated digests.
"""
import sqlite3
from typing import Optional


class MemoryStore:
    """Read/write interface over the memory and session_log tables in trades.db."""

    def __init__(self, db_path: str = "/data/trades/trades.db"):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def read(self, key: str) -> Optional[str]:
        """Return the value for key, or None if key does not exist."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM memory WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def write(self, key: str, value: str) -> None:
        """Upsert key → value. Creates the row if it doesn't exist."""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=datetime('now')",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    def read_all(self) -> dict:
        """Return all memory rows as {key: value}."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT key, value FROM memory").fetchall()
            return {row["key"]: row["value"] for row in rows}
        finally:
            conn.close()

    def log_session(self, session_name: str, date: str, raw_response: str) -> int:
        """Append a session record. Returns the new row id for later digest update."""
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO session_log (session_name, date, raw_response) "
                "VALUES (?, ?, ?)",
                (session_name, date, raw_response),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_session_digest(self, session_id: int, digest: str) -> None:
        """Write the Haiku-generated digest for a previously logged session."""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE session_log SET digest = ? WHERE id = ?",
                (digest, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent_digests(self, n: int = 2) -> list:
        """Return the last n digested sessions in chronological order (oldest first).

        Only returns rows where digest IS NOT NULL.
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_name, date, digest FROM session_log "
                "WHERE digest IS NOT NULL "
                "ORDER BY logged_at DESC LIMIT ?",
                (n,),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]
        finally:
            conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_memory.py -v
```
Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scheduler/memory.py tests/test_memory.py
git commit -m "feat: add MemoryStore class for SQLite-backed memory blocks and session log"
```

---

## Task 3: SessionDigester

Haiku 4.5 post-session summarizer. Produces a structured 4-section digest capturing decisions made (with why), decisions skipped (with why and what would change that), open uncertainties, and key conditions to watch.

**Files:**
- Create: `scheduler/digester.py`
- Create: `tests/test_digester.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_digester.py
from unittest.mock import MagicMock, patch
import pytest


def test_summarize_calls_haiku_with_correct_model():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="DECISIONS MADE:\n- Opened NVDA long at $875")]
    mock_client.messages.create.return_value = mock_response

    from scheduler.digester import SessionDigester
    digester = SessionDigester(api_key="test-key", _client=mock_client)
    result = digester.summarize("pre_market session response text", "pre_market")

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert call_kwargs["max_tokens"] == 400
    assert "DECISIONS MADE" in result


def test_summarize_truncates_long_responses():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="short digest")]
    mock_client.messages.create.return_value = mock_response

    from scheduler.digester import SessionDigester, DIGEST_MAX_CHARS
    digester = SessionDigester(api_key="test-key", _client=mock_client)
    long_response = "x" * (DIGEST_MAX_CHARS + 1000)
    digester.summarize(long_response, "eod_reflection")

    sent_content = mock_client.messages.create.call_args[1]["messages"][0]["content"]
    assert len(sent_content) <= DIGEST_MAX_CHARS + 500  # prompt template overhead


def test_summarize_returns_empty_string_on_error():
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API error")

    from scheduler.digester import SessionDigester
    digester = SessionDigester(api_key="test-key", _client=mock_client)
    result = digester.summarize("any response", "health_check")

    assert result == ""


def test_summarize_includes_session_name_in_prompt():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="digest text")]
    mock_client.messages.create.return_value = mock_response

    from scheduler.digester import SessionDigester
    digester = SessionDigester(api_key="test-key", _client=mock_client)
    digester.summarize("response", "weekly_review")

    sent_content = mock_client.messages.create.call_args[1]["messages"][0]["content"]
    assert "weekly_review" in sent_content
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_digester.py -v
```
Expected: `FAILED` — `scheduler.digester` not found.

- [ ] **Step 3: Create scheduler/digester.py**

```python
# scheduler/digester.py
"""Haiku-powered session digest generator.

After each session, summarizes the agent's response into a structured 4-section
digest preserving the reasoning chain: what was decided and why, what was skipped
and why, open uncertainties, and key conditions to watch.
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)

DIGEST_MAX_CHARS = 6000

DIGEST_PROMPT_TEMPLATE = """You are summarizing a trading session for the AI fund manager that will run the NEXT session.
Your summary must preserve the reasoning chain so the next session starts with full context.

Session type: {session_name}
Session response:
{response}

Write a structured digest with exactly these 4 sections. Be specific — include tickers, prices, indicator values, and conditions wherever they appear in the response.

DECISIONS MADE:
For each action taken: what was done, why (specific signals/data that justified it), and what conditions would invalidate it.
Example: "Opened NVDA long at $875 — RSI 58 pullback with ADX 31 confirming trend, volume 1.8x avg. Thesis breaks if price closes below 9-EMA ($869) or volume dries up."

DECISIONS NOT MADE:
For each setup considered but skipped: what it was, why it was passed on, and what would need to change to make it actionable.
Example: "Skipped TSLA short — setup valid but VIX spiking fast, regime shift risk. Would reconsider if VIX stabilizes and TSLA breaks below $188 support on volume."

OPEN UNCERTAINTIES:
What was unclear, ambiguous, or being monitored. What information would resolve it.
Example: "AAPL thesis unclear — strong RS but earnings tomorrow. Watching after-hours reaction before forming a view."

KEY CONDITIONS TO WATCH:
Specific price levels, events, or signals that matter for active positions or pending setups.
Example: "NVDA: 9-EMA at $869 is the line. SPY: needs to hold $520 for bull thesis to remain intact."

Keep each section to 2-4 bullet points. Total output under 350 words."""


class SessionDigester:
    """Summarizes a session response into a structured digest using Haiku 4.5."""

    def __init__(self, api_key: str, _client=None):
        if _client is not None:
            self.client = _client
        else:
            import anthropic
            self.client = anthropic.Anthropic(api_key=api_key)

    def summarize(self, raw_response: str, session_name: str) -> str:
        """Return a 4-section structured digest. Returns empty string on any error."""
        try:
            truncated = raw_response[:DIGEST_MAX_CHARS]
            prompt = DIGEST_PROMPT_TEMPLATE.format(
                session_name=session_name,
                response=truncated,
            )
            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as exc:
            log.warning("Session digest failed for %s: %s", session_name, exc)
            return ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_digester.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scheduler/digester.py tests/test_digester.py
git commit -m "feat: add SessionDigester using Haiku 4.5 for post-session reasoning summaries"
```

---

## Task 4: AgentCore

The core replacement for `LettaTraderAgent`. Handles prompt construction with caching, the tool call loop, memory interface, and background digest triggering. This is the heart of the architecture change.

**Files:**
- Rewrite: `scheduler/agent.py`
- Rewrite: `tests/test_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent.py
import json
import threading
from unittest.mock import MagicMock, patch, call
import pytest


@pytest.fixture
def mock_memory(tmp_path):
    """MemoryStore backed by a real temp db with all 5 blocks pre-seeded."""
    import scheduler.tools.sqlite as sqlite_mod
    db_path = str(tmp_path / "test.db")
    original = sqlite_mod.DB_PATH
    sqlite_mod.DB_PATH = db_path
    sqlite_mod.bootstrap_db()
    sqlite_mod.bootstrap_memory_table({
        "strategy_doc": "test strategy",
        "watchlist": "test watchlist",
        "performance_snapshot": "{}",
        "today_context": "test context",
        "observations": "test observations",
    })
    sqlite_mod.DB_PATH = original
    from scheduler.memory import MemoryStore
    return MemoryStore(db_path=db_path)


def _make_end_turn_response(text: str):
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [mock_block]
    return response


def _make_tool_use_response(tool_name: str, tool_input: dict, tool_id: str = "tool_1"):
    mock_tool = MagicMock()
    mock_tool.type = "tool_use"
    mock_tool.name = tool_name
    mock_tool.input = tool_input
    mock_tool.id = tool_id
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [mock_tool]
    return response


def test_run_session_returns_text_on_end_turn(mock_memory):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_end_turn_response('{"session": "pre_market"}')
    mock_digester = MagicMock()
    mock_digester.summarize.return_value = "digest text"

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mock_memory.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mock_memory,
    )
    result = agent.run_session("pre_market", "SESSION: pre_market | DATE: 2026-04-16")
    assert result == '{"session": "pre_market"}'


def test_run_session_executes_tool_call_and_continues(mock_memory):
    mock_client = MagicMock()
    # First response: tool_use; second response: end_turn
    mock_client.messages.create.side_effect = [
        _make_tool_use_response("trade_query", {"sql": "SELECT COUNT(*) FROM trades"}),
        _make_end_turn_response('{"session": "health_check", "summary": "done"}'),
    ]
    mock_digester = MagicMock()
    mock_digester.summarize.return_value = ""

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mock_memory.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mock_memory,
    )

    with patch("scheduler.agent._execute_tool", return_value=[{"count": 0}]) as mock_exec:
        result = agent.run_session("health_check", "SESSION: health_check")

    assert mock_client.messages.create.call_count == 2
    mock_exec.assert_called_once_with("trade_query", {"sql": "SELECT COUNT(*) FROM trades"})
    assert "health_check" in result


def test_run_session_logs_response_to_session_log(mock_memory):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_end_turn_response("response text")
    mock_digester = MagicMock()
    mock_digester.summarize.return_value = "a digest"

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mock_memory.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mock_memory,
    )
    agent.run_session("pre_market", "prompt")
    # Give background thread time to write digest
    import time; time.sleep(0.2)
    digests = mock_memory.get_recent_digests(n=5)
    assert len(digests) == 1
    assert digests[0]["digest"] == "a digest"


def test_update_memory_block_writes_to_store(mock_memory):
    mock_client = MagicMock()
    mock_digester = MagicMock()

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mock_memory.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mock_memory,
    )
    agent.update_memory_block("strategy_doc", "new strategy text")
    assert mock_memory.read("strategy_doc") == "new strategy text"


def test_get_memory_block_reads_from_store(mock_memory):
    mock_client = MagicMock()
    mock_digester = MagicMock()

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mock_memory.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mock_memory,
    )
    result = agent.get_memory_block("watchlist")
    assert result == "test watchlist"


def test_system_prompt_has_cache_control_on_static_block(mock_memory):
    from scheduler.agent import build_system_prompt
    blocks = {
        "strategy_doc": "doc", "watchlist": "wl",
        "performance_snapshot": "{}", "today_context": "ctx", "observations": "obs"
    }
    system = build_system_prompt(blocks)
    assert isinstance(system, list)
    assert len(system) == 2
    # First block (static) must have cache_control
    assert system[0].get("cache_control") == {"type": "ephemeral"}
    # Second block (dynamic memory) must NOT have cache_control
    assert "cache_control" not in system[1]
    # Dynamic block must contain all 5 memory values
    assert "doc" in system[1]["text"]
    assert "wl" in system[1]["text"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_agent.py -v
```
Expected: `FAILED` — `AgentCore` not found.

- [ ] **Step 3: Rewrite scheduler/agent.py**

Replace the entire file with:

```python
# scheduler/agent.py
"""Direct Anthropic SDK agent core. Replaces LettaTraderAgent.

AgentCore handles:
- Building the two-tier cached system prompt (static manual + dynamic memory blocks)
- Running the tool call loop until stop_reason == "end_turn"
- Logging session responses and triggering background Haiku digests
- Exposing get/update_memory_block for strategy_gate.py compatibility
"""
import json
import logging
import os
import threading
from datetime import date
from typing import Optional

from scheduler.memory import MemoryStore
from scheduler.digester import SessionDigester

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Static system prompt — cached by Anthropic after first call (~8,500 tokens)
# Edit this constant to update Claude's operating manual.
# ---------------------------------------------------------------------------
STATIC_PROMPT = """\
# ClaudeTrading Operations Manual

## Who You Are

You are the intelligence engine for an autonomous AI trading system managing a $50,000 Alpaca paper trading account. You are not an assistant — you are an active fund manager. Every session you analyze market conditions, make execution decisions, record your reasoning, and evolve your strategy through structured self-reflection.

You have full accountability for outcomes. No human approves individual trades. Your edge must be measurable, reproducible, and improving.

## Account Parameters

Starting equity: $50,000 paper account via Alpaca Markets
Universe: US equities only — no options, futures, crypto, or foreign securities
Directions: Long (buy) and short (sell) both available
Sessions: Fixed cron schedule set by the scheduler — you cannot self-trigger

Hard position limits (defaults — evolvable via strategy gate):
- Max open positions: 5 simultaneously
- Max single position size: 15% of equity
- Risk per trade: 1% of equity
- Stop loss default: 1.5x ATR below entry (long) / above entry (short)
- Profit target default: 3x ATR from entry (minimum 2:1 R:R required)
- Max daily loss: 3% of equity — halt new positions if breached, no exceptions

Position sizing:
  shares = (equity * risk_per_trade_pct) / abs(entry_price - stop_loss)
  position_value = shares * entry_price
  Reject if position_value > equity * max_position_pct

## Memory System

You maintain five persistent memory blocks injected at session start under "Your Current State."

strategy_doc: Your trading rulebook. NEVER write to it directly. Changes via proposed_change only.
watchlist: Current candidates. Max 12 entries. Format: TICKER | thesis | date | confidence | entry zone | stop | target
performance_snapshot: JSON with win_rate_10, win_rate_20, avg_rr, equity, drawdown, pivot_alerts. Refresh from trade_query at EOD only.
today_context: Pre-market analysis. Written in pre_market. Read in market_open. Reset each day.
observations: Rolling field notes. Max 15 bullets. Format: [YYYY-MM-DD] Text in <=15 words.

## Session Responsibilities

### pre_market (6:00 AM ET)
1. alpaca_get_account — verify equity
2. fmp_ohlcv("SPY", limit=60) + fmp_ohlcv("VIX", limit=60) then run_script with market_regime_detector
3. fmp_screener to find candidates (volume > 1M, mkt_cap > 2B)
4. For each candidate: fmp_ohlcv(limit=20), run_script indicators, fmp_news, fmp_earnings_calendar
5. Write today_context with regime + top 5-10 setups
6. Write watchlist (max 12)
7. hypothesis_log new theses as "formed"

### market_open (9:30 AM ET)
1. Review today_context and watchlist
2. Check recent_context for live positions — skip already-held tickers
3. For each trade where conditions are met:
   a. Compute shares via sizing formula
   b. trade_open(...) FIRST — get trade_id
   c. alpaca_place_order(...)
   d. hypothesis_log(id, "testing", f"Opened trade_id {trade_id} at {entry}")
4. Skip trades where price is outside entry zone — do not chase

CRITICAL: trade_open BEFORE alpaca_place_order. If order fails after trade_open succeeds,
call trade_close(trade_id, 0, "order_failed", 0, 0) immediately.
No proposed_change in market_open — system rejects it.

### health_check (1:00 PM ET)
1. Review positions from recent_context
2. For each position: is the thesis still intact?
3. Close if: stop hit, thesis invalidated by news/structure, or cannot state why trade is still valid
   Close sequence: alpaca_place_order -> trade_close -> hypothesis_log update
4. Seek new setups only if buying_power > 0 AND positions < 5 AND clear setup
No proposed_change in health_check — system rejects it.

### eod_reflection (3:45 PM ET)
1. Close remaining open positions (unless overnight hold explicitly justified in today_context)
2. trade_query to compute win_rate_10, win_rate_20, avg_rr — update performance_snapshot
3. Write new observations (<=15 words, date-tagged)
4. If pattern across >=3 trades: emit proposed_change
5. Reset today_context to "Cleared."

### weekly_review (6:00 PM Sunday)
1. Comprehensive trade_query: win rates by setup_type, regime, VIX range, hypothesis
2. Confirm (>=10 trades, positive avg_r) or reject (negative avg_r) hypotheses
3. Compress observations to <=10 bullets
4. Compress watchlist — remove expired theses
5. Update performance_snapshot
6. proposed_change if major pattern found

## Tool Reference

### Market Data
fmp_screener(market_cap_more_than, volume_more_than, exchange, limit)
fmp_ohlcv(ticker, limit=20) — use limit=60 for market_regime_detector (needs MA50)
fmp_news(tickers, limit=10)
fmp_earnings_calendar(from_date, to_date)
serper_search(query)

### Code Execution
run_script(code, timeout=30, scripts_dir="/app/scripts")
- NO API credentials inside scripts — pre-fetch data with fmp_ohlcv first
- Embed fetched data as Python variables in the script string
- End scripts with: print(json.dumps(result))

Indicator scripts (/app/scripts/indicators/):
  rsi.py -> compute_rsi(closes, period=14) -> {rsi, oversold, overbought}
  macd.py -> compute_macd(closes) -> {macd, signal, histogram, crossover}
  rate_of_change.py -> compute_roc(closes, period=10) -> {roc}
  ema_crossover.py -> compute_ema_crossover(closes, fast=9, slow=21) -> {ema_fast, ema_slow, cross}
  adx_trend_strength.py -> compute_adx(highs, lows, closes, period=14) -> {adx, trend_strength}
  supertrend.py -> compute_supertrend(highs, lows, closes) -> {direction, level}
  atr.py -> compute_atr(highs, lows, closes, period=14) -> {atr, atr_pct}
  bollinger_bands.py -> compute_bb(closes) -> {upper, middle, lower, width, pct_b}
  vix_percentile.py -> compute_vix_percentile(vix_closes) -> {percentile, regime}
  vwap.py -> compute_vwap(highs, lows, closes, volumes) -> {vwap, distance_pct}
  obv.py -> compute_obv(closes, volumes) -> {obv, trend}
  volume_profile.py -> compute_volume_profile(closes, volumes) -> {poc, value_area_high, value_area_low}
  market_regime_detector.py -> detect_regime(spy_ohlcv, vix_ohlcv) -> {regime, vix_percentile, breadth, trend_slope}
  relative_strength_scanner.py -> scan_rs(ticker_ohlcv_dict, benchmark_ohlcv) -> {rankings}

### Execution
alpaca_get_account()
alpaca_get_positions()
alpaca_place_order(symbol, qty, side, order_type="market", time_in_force="day", limit_price=None, stop_price=None)
alpaca_list_orders(status="open", limit=50)
alpaca_cancel_order(order_id)

### Record Keeping (Required)
trade_open(ticker, side, entry_price, size, setup_type, hypothesis_id, rationale,
           vix_at_entry, regime, stop_loss=None, take_profit=None, context_json=None)
  context_json must be a JSON string with indicator values at entry:
  {"rsi": 63.2, "adx": 28.1, "atr": 3.45, "atr_pct": 0.034, "volume_ratio": 1.8,
   "vix_percentile": 42.0, "macd_histogram": 0.23, "distance_from_vwap_pct": 0.012,
   "supertrend_direction": "up"}
  Include any indicator you actually computed. These values are queryable via filter_sql.

trade_close(trade_id, exit_price, exit_reason, outcome_pnl, r_multiple)
  r_multiple = outcome_pnl / (abs(entry_price - stop_loss) * size)
  exit_reason: hit_target | stop_hit | thesis_invalidated | time_exit | manual | order_failed

hypothesis_log(hypothesis_id, event_type, body)
  event_type: formed | testing | confirmed | rejected | refined
  IDs: H001, H002, H003... never reuse

trade_query(sql) — SELECT only. Blocked: INSERT UPDATE DELETE DROP ALTER CREATE PRAGMA

Useful queries:
  SELECT setup_type, COUNT(*) n, AVG(r_multiple) avg_r,
         SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END)*1.0/COUNT(*) win_rate
  FROM trades WHERE closed_at IS NOT NULL GROUP BY setup_type ORDER BY avg_r DESC;

  SELECT ticker, side, setup_type, r_multiple, exit_reason, closed_at
  FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 10;

## Strategy Evolution Gate

Valid only in: eod_reflection, weekly_review.
Invalid in: pre_market, market_open, health_check — system ignores proposed_change there.
One change at a time — check recent_context for current probationary status first.

proposed_change format:
  "proposed_change": {
    "description": "What is changing and why, referencing trade data",
    "new_strategy_doc": "Full strategy_doc replacement text — complete document",
    "filter_sql": "WHERE clause FRAGMENT only — no WHERE keyword, no SELECT, no LIMIT"
  }

filter_sql rules:
  Valid:   setup_type = 'momentum' AND json_extract(context_json, '$.rsi') < 65
  Valid:   vix_at_entry < 25 AND regime != 'bear_high_vol'
  Invalid: WHERE setup_type = 'momentum'     <- contains WHERE
  Invalid: SELECT * FROM trades WHERE ...    <- full SQL statement
  Omit filter_sql for qualitative changes (new setup types, session behavior).

## Risk Management

Daily halt: If today's closed trade P&L sum < -3% equity, stop opening new positions.
Every trade must have a defined stop before entry — no exceptions.
Health check: If you cannot state in one sentence why a position is still valid, close it.
Overnight: Default close before 3:50 PM ET. To hold overnight, write explicit justification in today_context.
Correlation: Max 2 positions in same sector simultaneously.

## JSON Response Format

Every session response must contain a valid JSON object:
{
  "session": "session_name",
  "date": "YYYY-MM-DD",
  "summary": "One paragraph summary of decisions and reasoning",
  "actions_taken": ["list of actions"],
  "proposed_change": null,
  "errors": []
}

For market_open, include:
  "trades_opened": [{"ticker": "X", "trade_id": N, "side": "buy", "size": N, "entry": N, "stop": N, "target": N}]
  "trades_skipped": [{"ticker": "X", "reason": "..."}]

For eod_reflection, include:
  "performance_update": {"win_rate_10": N, "avg_rr": N, "current_equity": N}

Errors go in errors[] — scheduler forwards non-empty errors to Telegram.

## Hard Constraints

- trade_query is read-only: INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA blocked
- run_script has no API credentials: do not call FMP/Alpaca/Serper inside scripts
- run_script: 30s timeout, 256MB RAM
- API calls: 30s timeout
- Strategy gate backtest: 60 days maximum
- One proposed_change in probation at a time
- proposed_change processed only in eod_reflection and weekly_review
- This system is stateless between sessions — no conversation history carries over
- You cannot self-trigger sessions or schedule future actions
"""


# ---------------------------------------------------------------------------
# Tool schemas — Anthropic API format
# ---------------------------------------------------------------------------
TOOL_SCHEMAS = [
    {
        "name": "trade_open",
        "description": "Record a new trade at entry time. Call BEFORE placing the Alpaca order. Returns trade_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Stock ticker symbol, e.g. NVDA"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "entry_price": {"type": "number"},
                "size": {"type": "number", "description": "Number of shares"},
                "setup_type": {"type": "string", "description": "e.g. momentum, mean_reversion, breakout"},
                "hypothesis_id": {"type": "string", "description": "e.g. H001"},
                "rationale": {"type": "string"},
                "vix_at_entry": {"type": "number"},
                "regime": {"type": "string", "description": "e.g. bull_low_vol"},
                "stop_loss": {"type": "number"},
                "take_profit": {"type": "number"},
                "context_json": {"type": "string", "description": "JSON string of indicator values at entry"},
            },
            "required": ["ticker", "side", "entry_price", "size", "setup_type",
                         "hypothesis_id", "rationale", "vix_at_entry", "regime"],
        },
    },
    {
        "name": "trade_close",
        "description": "Stamp exit fields onto an open trade after the exit order fills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "trade_id": {"type": "integer"},
                "exit_price": {"type": "number"},
                "exit_reason": {"type": "string", "enum": ["hit_target", "stop_hit", "thesis_invalidated", "time_exit", "manual", "order_failed"]},
                "outcome_pnl": {"type": "number"},
                "r_multiple": {"type": "number"},
            },
            "required": ["trade_id", "exit_price", "exit_reason", "outcome_pnl", "r_multiple"],
        },
    },
    {
        "name": "hypothesis_log",
        "description": "Append a lifecycle event to the hypothesis ledger.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis_id": {"type": "string"},
                "event_type": {"type": "string", "enum": ["formed", "testing", "confirmed", "rejected", "refined"]},
                "body": {"type": "string"},
            },
            "required": ["hypothesis_id", "event_type", "body"],
        },
    },
    {
        "name": "trade_query",
        "description": "Execute a read-only SELECT query against the trades and hypothesis_log tables.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "alpaca_get_account",
        "description": "Get Alpaca account information: equity, buying_power, cash.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "alpaca_get_positions",
        "description": "Get all open positions with symbol, qty, avg_entry_price, unrealized_pl.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "alpaca_place_order",
        "description": "Place a buy or sell order. Call trade_open FIRST to get trade_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "qty": {"type": "number"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "order_type": {"type": "string", "enum": ["market", "limit", "stop", "stop_limit"], "default": "market"},
                "time_in_force": {"type": "string", "enum": ["day", "gtc", "opg", "cls", "ioc", "fok"], "default": "day"},
                "limit_price": {"type": "number"},
                "stop_price": {"type": "number"},
            },
            "required": ["symbol", "qty", "side"],
        },
    },
    {
        "name": "alpaca_list_orders",
        "description": "List orders by status. Use to confirm limit order fills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "alpaca_cancel_order",
        "description": "Cancel an open order by its UUID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "fmp_screener",
        "description": "Screen US stocks by market cap and volume.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_cap_more_than": {"type": "integer", "default": 1000000000},
                "volume_more_than": {"type": "integer", "default": 500000},
                "exchange": {"type": "string", "default": "NYSE,NASDAQ"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
    },
    {
        "name": "fmp_ohlcv",
        "description": "Get daily OHLCV for a ticker. Default 20 days. Use limit=60 for market_regime_detector.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "fmp_news",
        "description": "Get recent news articles for a list of tickers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["tickers"],
        },
    },
    {
        "name": "fmp_earnings_calendar",
        "description": "Get scheduled earnings between two dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "YYYY-MM-DD"},
                "to_date": {"type": "string", "description": "YYYY-MM-DD"},
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "serper_search",
        "description": "Google search for news, macro context, SEC filings, analyst ratings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_script",
        "description": "Execute Python in a sandboxed subprocess. Pre-fetch all data before calling. No API credentials inside scripts. End with print(json.dumps(result)).",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout": {"type": "integer", "default": 30},
                "scripts_dir": {"type": "string", "default": "/app/scripts"},
            },
            "required": ["code"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------
def _build_tool_functions() -> dict:
    from scheduler.tools.sqlite import trade_open, trade_close, hypothesis_log, trade_query
    from scheduler.tools.alpaca import (
        alpaca_get_account, alpaca_get_positions, alpaca_place_order,
        alpaca_list_orders, alpaca_cancel_order,
    )
    from scheduler.tools.fmp import fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar
    from scheduler.tools.serper import serper_search
    from scheduler.tools.pyexec import run_script
    return {
        "trade_open": trade_open,
        "trade_close": trade_close,
        "hypothesis_log": hypothesis_log,
        "trade_query": trade_query,
        "alpaca_get_account": alpaca_get_account,
        "alpaca_get_positions": alpaca_get_positions,
        "alpaca_place_order": alpaca_place_order,
        "alpaca_list_orders": alpaca_list_orders,
        "alpaca_cancel_order": alpaca_cancel_order,
        "fmp_screener": fmp_screener,
        "fmp_ohlcv": fmp_ohlcv,
        "fmp_news": fmp_news,
        "fmp_earnings_calendar": fmp_earnings_calendar,
        "serper_search": serper_search,
        "run_script": run_script,
    }


def _execute_tool(name: str, input_dict: dict):
    """Dispatch a tool call by name. Returns the result or an error dict."""
    fns = _build_tool_functions()
    fn = fns.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**input_dict)
    except Exception as exc:
        log.error("Tool %s failed: %s", name, exc)
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------
def build_system_prompt(blocks: dict) -> list:
    """Return the two-tier system prompt list for the Anthropic messages API.

    Tier 1 (static): Full operations manual with cache_control — cached after first call.
    Tier 2 (dynamic): Current memory block values — re-injected fresh each session.
    """
    dynamic_text = f"""## Your Current State
[Values read from MemoryStore at session start — written back by you each session]

### STRATEGY_DOC
{blocks.get('strategy_doc', 'Not set.')}

### WATCHLIST
{blocks.get('watchlist', 'Not set.')}

### PERFORMANCE_SNAPSHOT
{blocks.get('performance_snapshot', 'Not set.')}

### TODAY_CONTEXT
{blocks.get('today_context', 'Not set.')}

### OBSERVATIONS
{blocks.get('observations', 'Not set.')}"""

    return [
        {
            "type": "text",
            "text": STATIC_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_text,
        },
    ]


def _extract_text(response) -> str:
    """Extract all text blocks from a message response."""
    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# AgentCore
# ---------------------------------------------------------------------------
MAX_TOOL_ITERATIONS = 25


class AgentCore:
    """Stateless session runner backed by direct Anthropic SDK calls.

    Interface is intentionally compatible with LettaTraderAgent so that
    strategy_gate.py and main.py require minimal changes:
      - run_session(prompt) replaces send_session(prompt)
      - get_memory_block / update_memory_block are identical
    """

    def __init__(
        self,
        db_path: str = "/data/trades/trades.db",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        _client=None,
        _digester=None,
        _memory: Optional[MemoryStore] = None,
    ):
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.memory = _memory or MemoryStore(db_path=db_path)

        if _client is not None:
            self.client = _client
        else:
            import anthropic
            self.client = anthropic.Anthropic(
                api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
            )

        if _digester is not None:
            self.digester = _digester
        else:
            self.digester = SessionDigester(
                api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
            )

    def run_session(self, session_name: str, user_message: str) -> str:
        """Run a full session: tool loop until end_turn. Returns last text response."""
        blocks = self.memory.read_all()
        system = build_system_prompt(blocks)
        messages = [{"role": "user", "content": user_message}]

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                text = _extract_text(response)
                log_id = self.memory.log_session(
                    session_name, date.today().isoformat(), text
                )
                threading.Thread(
                    target=self._run_digest,
                    args=(log_id, session_name, text),
                    daemon=True,
                ).start()
                return text

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = _execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                raise RuntimeError(f"Unexpected stop_reason: {response.stop_reason}")

        raise RuntimeError(f"Exceeded {MAX_TOOL_ITERATIONS} tool iterations in {session_name}")

    def _run_digest(self, log_id: int, session_name: str, raw_response: str) -> None:
        """Background thread: summarize session and store digest."""
        digest = self.digester.summarize(raw_response, session_name)
        if digest:
            self.memory.update_session_digest(log_id, digest)

    def get_memory_block(self, block_name: str) -> Optional[str]:
        """Read a named memory block. Used by strategy_gate.py."""
        return self.memory.read(block_name)

    def update_memory_block(self, block_name: str, value: str) -> None:
        """Write a named memory block. Used by strategy_gate.py."""
        self.memory.write(block_name, value)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_agent.py -v
```
Expected: all 6 tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```bash
pytest --tb=short -q
```
Expected: existing tests may fail due to `LettaTraderAgent` import — that's OK for now, handled in Task 8.

- [ ] **Step 6: Commit**

```bash
git add scheduler/agent.py tests/test_agent.py
git commit -m "feat: add AgentCore replacing LettaTraderAgent with direct Anthropic SDK"
```

---

## Task 5: Sessions Rewrite

Replace bare one-liner session prompts with rich structured prompts. Add `build_recent_context` helper that the scheduler calls to build the ephemeral per-session data block.

**Files:**
- Rewrite: `scheduler/sessions.py`
- Rewrite: `tests/test_sessions.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sessions.py
import json
import pytest
from scheduler.sessions import (
    build_pre_market_prompt,
    build_market_open_prompt,
    build_health_check_prompt,
    build_eod_reflection_prompt,
    build_weekly_review_prompt,
    build_recent_context,
)


def test_pre_market_prompt_contains_date_and_recent_context():
    ctx = "## Recent Context\n**Strategy Version:** v1 (confirmed)"
    result = build_pre_market_prompt("2026-04-16", "3h30m", ctx)
    assert "2026-04-16" in result
    assert "3h30m" in result
    assert "Recent Context" in result
    assert "pre_market" in result


def test_market_open_prompt_contains_trade_open_reminder():
    ctx = "## Recent Context\n**Live Positions:** 0 open"
    result = build_market_open_prompt("2026-04-16", "09:30", ctx)
    assert "trade_open" in result
    assert "BEFORE" in result.upper() or "before" in result


def test_health_check_prompt_contains_no_proposed_change_reminder():
    ctx = "## Recent Context\n**Live Positions:** 2 open"
    result = build_health_check_prompt("2026-04-16", ctx)
    assert "proposed_change" in result
    assert "health_check" in result


def test_eod_reflection_includes_trades_and_feedback():
    ctx = "## Recent Context\n**Strategy Version:** v2 (probationary)"
    trades = [{"ticker": "NVDA", "side": "buy", "r_multiple": 1.5}]
    result = build_eod_reflection_prompt("2026-04-16", trades, ctx, pending_feedback="Check RSI filter")
    assert "NVDA" in result
    assert "Check RSI filter" in result
    assert "proposed_change" in result


def test_eod_reflection_no_feedback_when_none():
    ctx = "## Recent Context"
    result = build_eod_reflection_prompt("2026-04-16", [], ctx, pending_feedback=None)
    assert "Pending Feedback" not in result


def test_weekly_review_includes_week_number():
    ctx = "## Recent Context"
    result = build_weekly_review_prompt("2026-04-16", 16, ctx)
    assert "16" in result
    assert "weekly_review" in result


def test_build_recent_context_formats_positions():
    positions = [{"symbol": "NVDA", "qty": "10", "avg_entry_price": "875.0", "unrealized_pl": "150.0"}]
    result = build_recent_context(
        last_trades=[],
        active_hypotheses=[],
        positions=positions,
        strategy_version="v1",
        strategy_status="confirmed",
        last_digests=[],
    )
    assert "NVDA" in result
    assert "v1" in result
    assert "confirmed" in result


def test_build_recent_context_includes_digests():
    digests = [
        {"session_name": "pre_market", "date": "2026-04-15", "digest": "DECISIONS MADE:\n- Opened NVDA"},
    ]
    result = build_recent_context(
        last_trades=[],
        active_hypotheses=[],
        positions=[],
        strategy_version="v1",
        strategy_status="confirmed",
        last_digests=digests,
    )
    assert "DECISIONS MADE" in result
    assert "pre_market" in result


def test_build_recent_context_shows_none_when_no_positions():
    result = build_recent_context(
        last_trades=[], active_hypotheses=[], positions=[],
        strategy_version="v1", strategy_status="confirmed", last_digests=[],
    )
    assert "None" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_sessions.py -v
```
Expected: `FAILED` — `build_recent_context` not found.

- [ ] **Step 3: Rewrite scheduler/sessions.py**

```python
# scheduler/sessions.py
import json
from typing import Optional


def build_recent_context(
    last_trades: list,
    active_hypotheses: list,
    positions: list,
    strategy_version: str,
    strategy_status: str,
    last_digests: list,
) -> str:
    """Build the ephemeral recent_context block injected into each session's user message."""
    trades_str = "\n".join(
        f"  {t.get('ticker', '?')} {t.get('side', '?')} "
        f"→ {float(t.get('r_multiple', 0)):+.2f}R "
        f"({t.get('exit_reason', '?')}) [{str(t.get('closed_at', ''))[:10]}]"
        for t in last_trades
    ) or "  None"

    hyp_str = "\n".join(
        f"  {h.get('hypothesis_id', '?')}: {str(h.get('body', ''))[:80]}"
        for h in active_hypotheses
    ) or "  None"

    pos_str = "\n".join(
        f"  {p.get('symbol', '?')} {p.get('qty', '?')}sh "
        f"@ ${float(p.get('avg_entry_price', 0)):.2f} | "
        f"unrealized: ${float(p.get('unrealized_pl', 0)):+.0f}"
        for p in positions
    ) or "  None"

    digest_section = ""
    if last_digests:
        digest_section = "\n**Previous Session Digests:**\n"
        for d in last_digests:
            digest_section += (
                f"[{d.get('session_name', '?')} {d.get('date', '?')}]\n"
                f"{d.get('digest', '')}\n\n"
            )

    return (
        f"## Recent Context (scheduler-injected)\n"
        f"**Strategy Version:** {strategy_version} ({strategy_status})\n"
        f"**Live Positions ({len(positions)} open):**\n{pos_str}\n"
        f"**Last 5 Closed Trades:**\n{trades_str}\n"
        f"**Active Hypotheses:**\n{hyp_str}"
        f"{digest_section}"
    )


def build_pre_market_prompt(date: str, market_opens_in: str, recent_context: str) -> str:
    return (
        f"SESSION: pre_market | DATE: {date} | MARKET_OPENS_IN: {market_opens_in}\n\n"
        f"{recent_context}\n\n"
        f"Begin pre_market session. Screen for today's opportunities, determine regime, "
        f"build watchlist. Respond with valid JSON."
    )


def build_market_open_prompt(date: str, time_et: str, recent_context: str) -> str:
    return (
        f"SESSION: market_open | DATE: {date} | TIME: {time_et} ET\n\n"
        f"{recent_context}\n\n"
        f"Market just opened. Execute planned trades from today_context and watchlist "
        f"where conditions are met. Remember: trade_open BEFORE alpaca_place_order. "
        f"No proposed_change in this session. Respond with valid JSON."
    )


def build_health_check_prompt(date: str, recent_context: str) -> str:
    return (
        f"SESSION: health_check | DATE: {date} | TIME: 13:00 ET\n\n"
        f"{recent_context}\n\n"
        f"Midday check. Review each open position against its original thesis. "
        f"Close positions where thesis is invalidated or stop has been hit. "
        f"No proposed_change in health_check — system rejects it. Respond with valid JSON."
    )


def build_eod_reflection_prompt(
    date: str,
    trades_today: list,
    recent_context: str,
    pending_feedback: Optional[str] = None,
) -> str:
    trades_json = json.dumps(trades_today)
    feedback_section = (
        f"\n**Pending Feedback from system/operator:** {pending_feedback}"
        if pending_feedback else ""
    )
    return (
        f"SESSION: eod_reflection | DATE: {date} | TIME: 15:45 ET\n\n"
        f"{recent_context}{feedback_section}\n\n"
        f"**Today's Trades (from scheduler):** {trades_json}\n\n"
        f"End of day. Close remaining positions (unless overnight hold explicitly justified "
        f"in today_context). Refresh performance_snapshot from trade_query. Write observations. "
        f"Propose strategy changes via proposed_change if patterns across >=3 trades justify it. "
        f"Respond with valid JSON including performance_update and proposed_change (or null)."
    )


def build_weekly_review_prompt(
    date: str,
    week_number: int,
    recent_context: str,
    pending_feedback: Optional[str] = None,
) -> str:
    feedback_section = (
        f"\n**Pending Feedback:** {pending_feedback}"
        if pending_feedback else ""
    )
    return (
        f"SESSION: weekly_review | DATE: {date} | WEEK: {week_number}\n\n"
        f"{recent_context}{feedback_section}\n\n"
        f"Weekly deep review. Mine trade data for patterns by setup_type, regime, VIX range, "
        f"and hypothesis. Confirm or reject hypotheses with sufficient data (>=10 trades). "
        f"Compress observations and watchlist. Update performance_snapshot. "
        f"Propose strategy changes if major patterns found. Respond with valid JSON."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_sessions.py -v
```
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scheduler/sessions.py tests/test_sessions.py
git commit -m "feat: rewrite session prompts with recent_context and build_recent_context helper"
```

---

## Task 6: Bootstrap Update

Remove Letta agent creation. Add memory table bootstrap with initial values. Keep strategy_versions seeding.

**Files:**
- Modify: `scheduler/bootstrap.py`
- Modify: `tests/test_bootstrap.py`

- [ ] **Step 1: Read and understand current test_bootstrap.py**

```bash
cat tests/test_bootstrap.py
```

- [ ] **Step 2: Rewrite scheduler/bootstrap.py**

```python
# scheduler/bootstrap.py
"""Bootstrap entrypoint.

Idempotent — safe to run on every container start.
1. Creates SQLite schema (trades, hypothesis_log, strategy_versions, memory, session_log)
2. Seeds memory table with initial values (skips if rows already exist)
3. Seeds v1 strategy_versions row (skips if exists)

Run manually: python -m scheduler.bootstrap
"""
import os
from pathlib import Path
from scheduler.agent import STATIC_PROMPT
from scheduler.tools.sqlite import (
    bootstrap_db as init_trades_db,
    bootstrap_memory_table,
    _connect as _db_connect,
)

_INITIAL_PERFORMANCE_SNAPSHOT = """{
  "trades_total": 0,
  "win_rate_10": null,
  "win_rate_20": null,
  "avg_rr": null,
  "current_drawdown_pct": 0.0,
  "peak_equity": 50000.0,
  "current_equity": 50000.0,
  "pivot_alerts": []
}"""

_V1_METADATA = (
    "## Version metadata\n"
    "version: v1\n"
    "status: confirmed\n"
    "promote_after: 20\n"
    "baseline_win_rate: null\n"
    "baseline_avg_r: null\n\n"
)

INITIAL_MEMORY_VALUES = {
    "strategy_doc": _V1_METADATA + STATIC_PROMPT,
    "watchlist": "# Watchlist\nEmpty on bootstrap — populated during pre_market sessions.\n",
    "performance_snapshot": _INITIAL_PERFORMANCE_SNAPSHOT,
    "today_context": "# Today's Context\nNot yet populated — will be written during pre_market session.\n",
    "observations": "# Observations\nEmpty on bootstrap — populated during EOD sessions.\n",
}


def bootstrap():
    print("Initialising SQLite trade store...")
    init_trades_db()
    print("Trade store ready.")

    print("Bootstrapping memory table...")
    bootstrap_memory_table(INITIAL_MEMORY_VALUES)
    print("Memory table ready.")

    # Seed v1 row in strategy_versions
    db_conn = _db_connect()
    try:
        if db_conn.execute(
            "SELECT version FROM strategy_versions WHERE version='v1'"
        ).fetchone() is None:
            v1_doc = _V1_METADATA + STATIC_PROMPT
            db_conn.execute(
                "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
                "VALUES ('v1', 'confirmed', ?, 20)",
                (v1_doc,),
            )
            db_conn.commit()
            print("Seeded strategy_versions with v1 (confirmed).")
        else:
            print("strategy_versions v1 already exists — skipping.")
    finally:
        db_conn.close()

    print("Bootstrap complete.")


if __name__ == "__main__":
    bootstrap()
```

- [ ] **Step 3: Update tests/test_bootstrap.py to match new bootstrap**

Replace the entire file with:

```python
# tests/test_bootstrap.py
import os
import tempfile
import pytest
from unittest.mock import patch


@pytest.fixture
def isolated_db(monkeypatch):
    """Give bootstrap a real temp db to write into."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    import scheduler.tools.sqlite as sqlite_mod
    monkeypatch.setattr(sqlite_mod, "DB_PATH", db_path)
    yield db_path
    os.unlink(db_path)


def test_bootstrap_creates_memory_table(isolated_db):
    from scheduler.bootstrap import bootstrap
    bootstrap()
    import sqlite3
    conn = sqlite3.connect(isolated_db)
    rows = conn.execute("SELECT key FROM memory").fetchall()
    keys = {r[0] for r in rows}
    assert "strategy_doc" in keys
    assert "watchlist" in keys
    assert "performance_snapshot" in keys
    assert "today_context" in keys
    assert "observations" in keys
    conn.close()


def test_bootstrap_seeds_strategy_versions_v1(isolated_db):
    from scheduler.bootstrap import bootstrap
    bootstrap()
    import sqlite3
    conn = sqlite3.connect(isolated_db)
    row = conn.execute(
        "SELECT status FROM strategy_versions WHERE version='v1'"
    ).fetchone()
    assert row is not None
    assert row[0] == "confirmed"
    conn.close()


def test_bootstrap_is_idempotent(isolated_db):
    from scheduler.bootstrap import bootstrap
    bootstrap()
    bootstrap()  # second call — should not raise or duplicate
    import sqlite3
    conn = sqlite3.connect(isolated_db)
    count = conn.execute("SELECT COUNT(*) FROM strategy_versions WHERE version='v1'").fetchone()[0]
    assert count == 1
    conn.close()


def test_bootstrap_does_not_overwrite_existing_memory(isolated_db):
    from scheduler.bootstrap import bootstrap
    bootstrap()
    import sqlite3
    conn = sqlite3.connect(isolated_db)
    conn.execute("UPDATE memory SET value='CUSTOM' WHERE key='watchlist'")
    conn.commit()
    conn.close()
    bootstrap()  # second call
    conn = sqlite3.connect(isolated_db)
    row = conn.execute("SELECT value FROM memory WHERE key='watchlist'").fetchone()
    assert row[0] == "CUSTOM"  # not overwritten
    conn.close()
```

- [ ] **Step 4: Run bootstrap tests**

```bash
pytest tests/test_bootstrap.py -v
```
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scheduler/bootstrap.py tests/test_bootstrap.py
git commit -m "feat: rewrite bootstrap to use MemoryStore, remove Letta agent creation"
```

---

## Task 7: main.py Update

Wire `AgentCore` into the scheduler. Build `recent_context` from SQLite + Alpaca before each session. Pass it to session prompt builders.

**Files:**
- Modify: `scheduler/main.py`

- [ ] **Step 1: Replace the Letta-specific parts in main.py**

Make these targeted changes to `scheduler/main.py`:

**a) Replace imports at top of file:**

Remove:
```python
from scheduler.agent import LettaTraderAgent
```
Add:
```python
from scheduler.agent import AgentCore
from scheduler.memory import MemoryStore
```

**b) Replace `get_agent()` function:**

Remove:
```python
def get_agent() -> LettaTraderAgent:
    if not AGENT_ID_FILE.exists():
        raise RuntimeError("No agent ID found. Run bootstrap first: python -m scheduler.bootstrap")
    return LettaTraderAgent(agent_id=AGENT_ID_FILE.read_text().strip())
```
Add:
```python
DB_PATH = os.environ.get("TRADES_DB_PATH", "/data/trades/trades.db")

def get_agent() -> AgentCore:
    return AgentCore(db_path=DB_PATH)
```

**c) Add `_build_recent_context()` helper after `_get_todays_trades()`:**

```python
def _build_recent_context_str() -> str:
    """Fetch all data needed for recent_context and build the injected string."""
    from scheduler.sessions import build_recent_context
    from scheduler.tools.sqlite import _connect as _db_connect

    # Last 5 closed trades from SQLite
    last_trades = []
    try:
        conn = _db_connect(read_only=True)
        rows = conn.execute(
            "SELECT ticker, side, r_multiple, exit_reason, closed_at "
            "FROM trades WHERE closed_at IS NOT NULL "
            "ORDER BY closed_at DESC LIMIT 5"
        ).fetchall()
        last_trades = [dict(r) for r in rows]
        conn.close()
    except Exception:
        pass

    # Active hypotheses (formed or testing, not confirmed/rejected)
    active_hypotheses = []
    try:
        conn = _db_connect(read_only=True)
        rows = conn.execute(
            "SELECT hypothesis_id, body FROM hypothesis_log "
            "WHERE event_type IN ('formed', 'testing') "
            "ORDER BY logged_at DESC"
        ).fetchall()
        # Deduplicate by hypothesis_id — keep most recent
        seen = set()
        for r in rows:
            if r["hypothesis_id"] not in seen:
                active_hypotheses.append(dict(r))
                seen.add(r["hypothesis_id"])
        conn.close()
    except Exception:
        pass

    # Live positions from Alpaca
    positions = _get_open_positions()

    # Current strategy version
    strategy_version = "v1"
    strategy_status = "confirmed"
    try:
        conn = _db_connect(read_only=True)
        row = conn.execute(
            "SELECT version, status FROM strategy_versions "
            "WHERE status IN ('confirmed', 'probationary') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            strategy_version = row["version"]
            strategy_status = row["status"]
        conn.close()
    except Exception:
        pass

    # Last 2 session digests
    last_digests = []
    try:
        memory = MemoryStore(db_path=DB_PATH)
        last_digests = memory.get_recent_digests(n=2)
    except Exception:
        pass

    return build_recent_context(
        last_trades=last_trades,
        active_hypotheses=active_hypotheses,
        positions=positions,
        strategy_version=strategy_version,
        strategy_status=strategy_status,
        last_digests=last_digests,
    )
```

**d) Update `run_session()` — replace `agent.send_session(prompt)` with `agent.run_session(prompt)`:**

Line ~123:
```python
raw_output = agent.send_session(prompt)
```
Change to:
```python
raw_output = agent.run_session(session_type, prompt)
```

Also update the pre-session snapshot logic — replace:
```python
pre_doc: Optional[str] = None
if is_strategy_session:
    try:
        pre_doc = get_agent().get_memory_block("strategy_doc")
    except Exception:
        pass
```
This stays the same — `get_memory_block` interface is preserved.

**e) Update each `job_*` function to pass `recent_context`:**

```python
def job_pre_market():
    now = datetime.now(ET)
    recent_context = _build_recent_context_str()
    prompt = build_pre_market_prompt(
        date=now.strftime("%Y-%m-%d"),
        market_opens_in="3h30m",
        recent_context=recent_context,
    )
    run_session("pre_market", prompt)


def job_market_open():
    now = datetime.now(ET)
    recent_context = _build_recent_context_str()
    prompt = build_market_open_prompt(
        date=now.strftime("%Y-%m-%d"),
        time_et="09:30",
        recent_context=recent_context,
    )
    run_session("market_open", prompt)


def job_health_check():
    now = datetime.now(ET)
    recent_context = _build_recent_context_str()
    prompt = build_health_check_prompt(
        date=now.strftime("%Y-%m-%d"),
        recent_context=recent_context,
    )
    run_session("health_check", prompt)


def job_eod_reflection():
    now = datetime.now(ET)
    trades = _get_todays_trades()
    pending_feedback = _read_and_clear_pending_feedback()
    recent_context = _build_recent_context_str()
    prompt = build_eod_reflection_prompt(
        date=now.strftime("%Y-%m-%d"),
        trades_today=trades,
        recent_context=recent_context,
        pending_feedback=pending_feedback,
    )
    run_session("eod_reflection", prompt)


def job_weekly_review():
    now = datetime.now(ET)
    week_num = now.isocalendar()[1]
    pending_feedback = _read_and_clear_pending_feedback()
    recent_context = _build_recent_context_str()
    prompt = build_weekly_review_prompt(
        date=now.strftime("%Y-%m-%d"),
        week_number=week_num,
        recent_context=recent_context,
        pending_feedback=pending_feedback,
    )
    run_session("weekly_review", prompt)
```

**f) Remove the `AGENT_ID_FILE` constant** (no longer used):

Remove line:
```python
AGENT_ID_FILE = Path("/app/state/.agent_id")
```

- [ ] **Step 2: Run tests that cover main.py**

```bash
pytest tests/test_main.py -v
```
Fix any test failures caused by the signature changes to session builders.

- [ ] **Step 3: Commit**

```bash
git add scheduler/main.py
git commit -m "feat: wire AgentCore into main scheduler, build recent_context per session"
```

---

## Task 8: fmp_ohlcv Default Change

Change the default data window from 90 days to 20 days.

**Files:**
- Modify: `scheduler/tools/fmp.py`
- Modify: `tests/test_tool_defaults.py` (if it tests the fmp_ohlcv default)

- [ ] **Step 1: Check the existing default test**

```bash
grep -n "fmp_ohlcv\|limit.*90\|limit.*20" tests/test_tool_defaults.py
```

- [ ] **Step 2: Update the default in fmp.py**

In `scheduler/tools/fmp.py`, line 46, change:
```python
def fmp_ohlcv(ticker: str, limit: int = 90, api_key: Optional[str] = None) -> dict:
```
to:
```python
def fmp_ohlcv(ticker: str, limit: int = 20, api_key: Optional[str] = None) -> dict:
```

Also update the docstring line:
```
limit: Number of trading days of history to return (default 90, changeable).
```
to:
```
limit: Number of trading days of history to return (default 20). Use limit=60 for market_regime_detector (needs MA50).
```

- [ ] **Step 3: Update test_tool_defaults.py to expect 20**

Find any test asserting the default is 90 and change to 20. If the test does not exist, add:

```python
def test_fmp_ohlcv_default_limit_is_20():
    import inspect
    from scheduler.tools.fmp import fmp_ohlcv
    sig = inspect.signature(fmp_ohlcv)
    assert sig.parameters["limit"].default == 20
```

- [ ] **Step 4: Run relevant tests**

```bash
pytest tests/test_tool_defaults.py tests/test_tools/ -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/fmp.py tests/test_tool_defaults.py
git commit -m "fix: change fmp_ohlcv default limit from 90 to 20 days"
```

---

## Task 9: Cleanup and Infrastructure

Remove Letta from requirements, Docker, and test config. Delete the now-unused registry.

**Files:**
- Delete: `scheduler/tools/registry.py`
- Modify: `requirements.txt`
- Modify: `docker-compose.yml`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Delete registry.py**

```bash
git rm scheduler/tools/registry.py
```

- [ ] **Step 2: Update requirements.txt**

Replace the contents of `requirements.txt` with:

```
anthropic>=0.50.0
apscheduler>=3.10.4
requests>=2.31.0
python-telegram-bot>=20.7
pandas>=2.1.0
numpy>=1.26.0
pytest>=7.4.0
pytest-mock>=3.12.0
responses>=0.24.0
python-dotenv>=1.0.0
```

(Removed: `letta>=0.6.0`. Added: `anthropic>=0.50.0`.)

- [ ] **Step 3: Update docker-compose.yml — remove letta service**

Open `docker-compose.yml`. Remove the entire `letta:` service block and the `letta-db:` volume entry. Add `ANTHROPIC_API_KEY` to the scheduler's environment section and remove `LETTA_SERVER_URL` and `OPENROUTER_*` variables.

The scheduler environment should include:
```yaml
environment:
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
  - ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-claude-sonnet-4-6}
  - FMP_API_KEY=${FMP_API_KEY}
  - SERPER_API_KEY=${SERPER_API_KEY}
  - ALPACA_API_KEY=${ALPACA_API_KEY}
  - ALPACA_SECRET_KEY=${ALPACA_SECRET_KEY}
  - ALPACA_BASE_URL=${ALPACA_BASE_URL:-https://paper-api.alpaca.markets}
  - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
  - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
```

- [ ] **Step 4: Update tests/conftest.py**

Replace the `set_test_env` fixture with:

```python
# tests/conftest.py
import os
import pytest


@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Set dummy env vars so tools don't require real keys in unit tests."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_anthropic_key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("FMP_API_KEY", "test_fmp_key")
    monkeypatch.setenv("SERPER_API_KEY", "test_serper_key")
    monkeypatch.setenv("ALPACA_API_KEY", "test_alpaca_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_alpaca_secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_tg_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test_tg_chat")
    monkeypatch.setenv("SCRIPTS_DIR", "scripts")
```

- [ ] **Step 5: Run full test suite**

```bash
pytest --tb=short -q
```
Expected: all tests PASS. If any tests still import from `scheduler.tools.registry` or `scheduler.agent.LettaTraderAgent`, fix those imports now.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: remove Letta — delete registry, update requirements and docker-compose"
```

---

## Task 10: Integration Verification

Confirm the complete system works end-to-end in a local dry run before deploying to VPS.

**Files:**
- No changes — verification only

- [ ] **Step 1: Run the full test suite with coverage**

```bash
pytest --cov=scheduler --cov-report=term-missing -q
```
Expected: all tests pass. Coverage on `scheduler/` should be above 80%.

- [ ] **Step 2: Test bootstrap locally**

```bash
ANTHROPIC_API_KEY=test FMP_API_KEY=test SERPER_API_KEY=test \
ALPACA_API_KEY=test ALPACA_SECRET_KEY=test TELEGRAM_BOT_TOKEN=test TELEGRAM_CHAT_ID=test \
python -c "
import scheduler.tools.sqlite as s
import tempfile, os
db = tempfile.mktemp(suffix='.db')
s.DB_PATH = db
s.bootstrap_db()
from scheduler.bootstrap import bootstrap, INITIAL_MEMORY_VALUES
from scheduler.tools.sqlite import bootstrap_memory_table
bootstrap_memory_table(INITIAL_MEMORY_VALUES)
from scheduler.memory import MemoryStore
m = MemoryStore(db_path=db)
print('All 5 keys:', list(m.read_all().keys()))
os.unlink(db)
print('Bootstrap verification passed.')
"
```
Expected output: `All 5 keys: ['strategy_doc', 'watchlist', 'performance_snapshot', 'today_context', 'observations']`

- [ ] **Step 3: Verify strategy_gate.py still works with AgentCore**

```bash
pytest tests/test_strategy_gate.py -v
```
Expected: all tests PASS (strategy_gate uses `agent.update_memory_block` and `agent.get_memory_block` — interface preserved).

- [ ] **Step 4: Final commit and tag**

```bash
git add -A
git commit -m "chore: phase 1 complete — Letta replaced with direct Anthropic SDK"
git tag v2.0.0-phase1
```

- [ ] **Step 5: Deploy to VPS**

Follow the `deploying-to-vps` skill. Key steps:
1. `git push origin main`
2. On VPS: `docker compose pull && docker compose build scheduler`
3. `docker compose down && docker compose up -d scheduler`
4. `docker compose exec scheduler python -m scheduler.bootstrap`
5. `docker compose logs -f scheduler` — verify first session fires cleanly

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered in task |
|---|---|
| Remove Letta entirely | Task 9 |
| AgentCore ~150 lines with tool loop | Task 4 |
| MemoryStore in SQLite memory table | Tasks 1, 2 |
| 5 memory blocks (including new observations) | Tasks 1, 2, 6 |
| Static system prompt with cache_control | Task 4 |
| Dynamic memory block without cache_control | Task 4 |
| recent_context in user message (not system prompt) | Tasks 5, 7 |
| Session digests (Haiku post-session) | Task 3, 4 |
| 4-section digest format | Task 3 |
| Digest injected into next session's recent_context | Tasks 5, 7 |
| fmp_ohlcv default 90 → 20 | Task 8 |
| Sessions.py enriched prompts | Task 5 |
| strategy_gate.py interface preserved | Task 4 (get/update_memory_block) |
| Bootstrap idempotent | Task 6 |
| docker-compose Letta removed | Task 9 |
| ANTHROPIC_API_KEY replaces OPENROUTER/LETTA vars | Tasks 6, 9 |

**Type consistency check:**
- `MemoryStore.log_session` returns `int` (log_id) → `AgentCore._run_digest` receives `int` ✓
- `build_recent_context` parameter names match `job_*` function calls in main.py ✓
- `run_session(session_name, user_message)` matches all `run_session(session_type, prompt)` call sites ✓
- `update_memory_block(block_name, value)` / `get_memory_block(block_name)` — same signature as LettaTraderAgent ✓

**Placeholder scan:** No TBDs found. All code blocks are complete and runnable.
