# Strategy Change Evaluation Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Intercept Claude's proposed strategy changes, pre-screen backtestable ones against 60 days of historical trades, and apply all changes as probationary versions that auto-promote or auto-revert based on real post-deployment performance.

**Architecture:** Claude emits `proposed_change` in its EOD/weekly session JSON instead of writing `strategy_doc` directly. The scheduler runs a SQL-based pre-screen for filterable changes, then applies all approved changes as probationary versions tracked in a new `strategy_versions` SQLite table. After a minimum number of closed trades under the new version, the scheduler auto-promotes or auto-reverts based on win rate and avg-R deltas. A fallback path detects direct writes and wraps them in probation automatically.

**Tech Stack:** Python 3.11, SQLite (WAL mode), APScheduler, Letta client API, pytest, pytest-mock

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `scheduler/tools/sqlite.py` | Modify | Schema: two new trade columns + `strategy_versions` table; updated `bootstrap_db` + `trade_open` |
| `scheduler/agent.py` | Modify | `update_memory_block` on shim + `LettaTraderAgent`; protocol instruction in `INITIAL_STRATEGY_DOC` |
| `scheduler/notifier.py` | Modify | Five new Telegram formatters |
| `scheduler/sessions.py` | Modify | `pending_feedback` parameter on EOD + weekly prompt builders |
| `scheduler/strategy_gate.py` | **Create** | `StrategyGateError`, `PreScreenResult`, `run_prescreen`, `snapshot_baseline_metrics`, `apply_change`, `check_probation`, `_append_feedback` |
| `scheduler/bootstrap.py` | Modify | Insert v1 seed row into `strategy_versions` after agent creation |
| `scheduler/main.py` | Modify | Read+clear pending feedback, intercept `proposed_change`, fallback detection, `check_probation` dispatch, Telegram notifications |
| `tests/test_tools/test_sqlite.py` | Modify | Tests for new columns and updated `trade_open` |
| `tests/test_agent.py` | Modify | Tests for `update_memory_block` |
| `tests/test_notifier.py` | Modify | Tests for five new formatters |
| `tests/test_sessions.py` | Modify | Tests for `pending_feedback` parameter |
| `tests/test_strategy_gate.py` | **Create** | All tests for `strategy_gate.py` |

---

### Task 1: Schema migration — sqlite.py

**Files:**
- Modify: `scheduler/tools/sqlite.py`
- Modify: `tests/test_tools/test_sqlite.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_tools/test_sqlite.py`:

```python
import json


def test_bootstrap_creates_strategy_versions_table(db):
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "strategy_versions" in tables


def test_bootstrap_adds_strategy_version_and_context_json_columns(db):
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    conn.close()
    assert "strategy_version" in cols
    assert "context_json" in cols


def test_bootstrap_is_idempotent_with_new_columns(db, monkeypatch):
    monkeypatch.setattr(sqlite_module, "DB_PATH", str(db))
    bootstrap_db()  # second call must not raise


def test_trade_open_stamps_strategy_version_from_table(db):
    # Seed a confirmed version row
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
        "VALUES ('v1', 'confirmed', 'doc', 20)"
    )
    conn.commit()
    conn.close()

    result = trade_open(
        ticker="AAPL", side="buy", entry_price=100.0, size=10.0,
        setup_type="momentum", hypothesis_id="H001", rationale="test",
        vix_at_entry=15.0, regime="bull",
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT strategy_version FROM trades WHERE id=?", (result["trade_id"],)
    ).fetchone()
    conn.close()
    assert row[0] == "v1"


def test_trade_open_stamps_none_when_strategy_versions_empty(db):
    result = trade_open(
        ticker="AAPL", side="buy", entry_price=100.0, size=10.0,
        setup_type="momentum", hypothesis_id="H001", rationale="test",
        vix_at_entry=15.0, regime="bull",
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT strategy_version FROM trades WHERE id=?", (result["trade_id"],)
    ).fetchone()
    conn.close()
    assert row[0] is None


def test_trade_open_stores_context_json(db):
    ctx = json.dumps({"rsi": 62.5, "adx": 28.0})
    result = trade_open(
        ticker="NVDA", side="buy", entry_price=500.0, size=5.0,
        setup_type="momentum", hypothesis_id="H002", rationale="test",
        vix_at_entry=18.0, regime="bull", context_json=ctx,
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT context_json FROM trades WHERE id=?", (result["trade_id"],)
    ).fetchone()
    conn.close()
    assert json.loads(row[0]) == {"rsi": 62.5, "adx": 28.0}
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_tools/test_sqlite.py::test_bootstrap_creates_strategy_versions_table \
       tests/test_tools/test_sqlite.py::test_trade_open_stamps_strategy_version_from_table -v
```
Expected: FAIL — `strategy_versions` table does not exist, columns missing.

- [ ] **Step 3: Update _SCHEMA in sqlite.py**

Replace the `_SCHEMA` constant:

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    side          TEXT    NOT NULL CHECK(side IN ('buy', 'sell')),
    entry_price   REAL    NOT NULL,
    exit_price    REAL,
    size          REAL    NOT NULL,
    setup_type    TEXT,
    hypothesis_id TEXT,
    rationale     TEXT,
    vix_at_entry  REAL,
    regime        TEXT,
    stop_loss     REAL,
    take_profit   REAL,
    outcome_pnl   REAL,
    r_multiple    REAL,
    exit_reason   TEXT,
    strategy_version TEXT,
    context_json  TEXT,
    opened_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at     TEXT
);

CREATE TABLE IF NOT EXISTS hypothesis_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis_id TEXT    NOT NULL,
    event_type    TEXT    NOT NULL
        CHECK(event_type IN ('formed','testing','confirmed','rejected','refined')),
    body          TEXT,
    logged_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    version           TEXT    PRIMARY KEY,
    status            TEXT    NOT NULL CHECK(status IN ('confirmed','probationary','reverted')),
    doc_text          TEXT    NOT NULL,
    baseline_win_rate REAL,
    baseline_avg_r    REAL,
    promote_after     INTEGER NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at       TEXT,
    revert_reason     TEXT
);
"""
```

- [ ] **Step 4: Update bootstrap_db to add columns idempotently**

Replace `bootstrap_db`:

```python
def bootstrap_db() -> None:
    """Create the trades-db schema. Idempotent — safe to call on every startup."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        # ALTER TABLE has no IF NOT EXISTS in SQLite — use try/except for idempotency
        for stmt in [
            "ALTER TABLE trades ADD COLUMN strategy_version TEXT",
            "ALTER TABLE trades ADD COLUMN context_json TEXT",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Update trade_open signature and body**

Replace `trade_open`:

```python
def trade_open(
    ticker: str,
    side: str,
    entry_price: float,
    size: float,
    setup_type: str,
    hypothesis_id: str,
    rationale: str,
    vix_at_entry: float,
    regime: str,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    context_json: Optional[str] = None,
) -> dict:
    """Record a new trade at entry time.

    Args:
        ticker: Stock ticker symbol (e.g. 'NVDA').
        side: Direction — 'buy' or 'sell'.
        entry_price: Fill price per share.
        size: Number of shares.
        setup_type: Setup label (e.g. 'momentum', 'mean_reversion').
        hypothesis_id: Hypothesis ID this trade is testing (e.g. 'H001').
        rationale: Free-text explanation of why this trade was taken.
        vix_at_entry: VIX level when the position was opened.
        regime: Market regime at entry (e.g. 'bull_low_vol').
        stop_loss: Stop-loss price passed to Alpaca, if any.
        take_profit: Take-profit price passed to Alpaca, if any.
        context_json: Optional JSON string with indicator values at entry time,
            e.g. '{"rsi": 63.2, "adx": 28.1}'. Used by strategy gate pre-screens.

    Returns:
        dict: {'trade_id': int} — pass this to trade_close when exiting.
    """
    _db_guard()
    conn = _connect()
    try:
        # Stamp current strategy version — reads from strategy_versions, not core memory
        version_row = conn.execute(
            "SELECT version FROM strategy_versions "
            "WHERE status IN ('confirmed', 'probationary') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        strategy_version = version_row["version"] if version_row else None

        cursor = conn.execute(
            """
            INSERT INTO trades
                (ticker, side, entry_price, size, setup_type, hypothesis_id,
                 rationale, vix_at_entry, regime, stop_loss, take_profit,
                 strategy_version, context_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, side, entry_price, size, setup_type, hypothesis_id,
             rationale, vix_at_entry, regime, stop_loss, take_profit,
             strategy_version, context_json),
        )
        conn.commit()
        return {"trade_id": cursor.lastrowid}
    finally:
        conn.close()
```

- [ ] **Step 6: Run all sqlite tests**

```
pytest tests/test_tools/test_sqlite.py -v
```
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_tools/test_sqlite.py
git commit -m "feat: add strategy_version, context_json columns and strategy_versions table"
```

---

### Task 2: agent.py — update_memory_block + protocol instruction

**Files:**
- Modify: `scheduler/agent.py`
- Modify: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_agent.py`:

```python
import pytest
from unittest.mock import MagicMock, Mock, patch
from scheduler.agent import LettaTraderAgent, _LettaClientShim, INITIAL_STRATEGY_DOC


def test_shim_update_memory_block_finds_block_and_calls_modify():
    mock_block = Mock()
    mock_block.label = "strategy_doc"
    mock_block.id = "block-abc"

    mock_letta = Mock()
    mock_letta.agents.blocks.list.return_value = [mock_block]

    shim = _LettaClientShim(mock_letta)
    shim.update_memory_block("agent-123", "strategy_doc", "updated text")

    mock_letta.agents.blocks.list.assert_called_once_with(agent_id="agent-123")
    mock_letta.blocks.modify.assert_called_once_with(block_id="block-abc", value="updated text")


def test_shim_update_memory_block_raises_if_block_not_found():
    mock_block = Mock()
    mock_block.label = "watchlist"
    mock_letta = Mock()
    mock_letta.agents.blocks.list.return_value = [mock_block]

    shim = _LettaClientShim(mock_letta)
    with pytest.raises(ValueError, match="strategy_doc"):
        shim.update_memory_block("agent-123", "strategy_doc", "text")


def test_agent_update_memory_block_delegates_to_shim():
    with patch("scheduler.agent.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        agent = LettaTraderAgent(agent_id="test-id")
        agent.update_memory_block("strategy_doc", "new value")

        mock_client.update_memory_block.assert_called_once_with(
            "test-id", "strategy_doc", "new value"
        )


def test_initial_strategy_doc_contains_protocol_instruction():
    assert "proposed_change" in INITIAL_STRATEGY_DOC
    assert "Strategy change protocol" in INITIAL_STRATEGY_DOC
    assert "Never write changes to this document directly" in INITIAL_STRATEGY_DOC
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_agent.py::test_shim_update_memory_block_finds_block_and_calls_modify \
       tests/test_agent.py::test_agent_update_memory_block_delegates_to_shim \
       tests/test_agent.py::test_initial_strategy_doc_contains_protocol_instruction -v
```
Expected: FAIL — methods not defined, instruction missing.

- [ ] **Step 3: Add update_memory_block to _LettaClientShim**

In `scheduler/agent.py`, add inside `_LettaClientShim` after `create_agent`:

```python
def update_memory_block(self, agent_id: str, block_name: str, value: str) -> None:
    """Update a named core memory block value via the Letta API."""
    blocks = self._client.agents.blocks.list(agent_id=agent_id)
    for block in blocks:
        if block.label == block_name:
            self._client.blocks.modify(block_id=block.id, value=value)
            return
    raise ValueError(f"Memory block '{block_name}' not found on agent {agent_id}")
```

- [ ] **Step 4: Add update_memory_block to LettaTraderAgent**

In `scheduler/agent.py`, add to `LettaTraderAgent` after `get_memory_block`:

```python
def update_memory_block(self, block_name: str, value: str) -> None:
    """Write a new value to a named core memory block via the Letta API."""
    self.client.update_memory_block(self.agent_id, block_name, value)
```

- [ ] **Step 5: Add strategy change protocol to INITIAL_STRATEGY_DOC**

In `scheduler/agent.py`, append the following to `INITIAL_STRATEGY_DOC` before the final `"""` closing the string — insert it between the `## Trade Record System` block and the `## Market Regime` line:

```python
# Insert this block in INITIAL_STRATEGY_DOC, before the "## Market Regime" line:
"""
## Strategy change protocol
Never write changes to this document directly. Emit proposed changes as
proposed_change in your session JSON output. The system will pre-screen
filterable changes against historical trade data and apply all changes
with version tracking. You will see the result — confirmed or reverted
with performance numbers — in your next session.

Proposed change format (in your session JSON):
  "proposed_change": {
    "description": "human-readable summary",
    "new_strategy_doc": "full updated strategy doc text (no version metadata block)",
    "filter_sql": "optional SQL condition — only for entry filters on trades table columns"
  }

filter_sql examples (uses context_json for indicator values):
  json_extract(context_json, '$.rsi') < 65 AND setup_type = 'momentum'
  regime != 'bear_high_vol'
  vix_at_entry < 25 AND setup_type = 'momentum'
Only include filter_sql if the change is a quantitative entry filter you can express as SQL.
"""
```

- [ ] **Step 6: Run all agent tests**

```
pytest tests/test_agent.py -v
```
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add scheduler/agent.py tests/test_agent.py
git commit -m "feat: add update_memory_block, strategy change protocol instruction"
```

---

### Task 3: notifier.py + sessions.py

**Files:**
- Modify: `scheduler/notifier.py`
- Modify: `scheduler/sessions.py`
- Modify: `tests/test_notifier.py`
- Modify: `tests/test_sessions.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_notifier.py`:

```python
from scheduler.notifier import (
    format_probation_start,
    format_promotion,
    format_revert,
    format_gate_blocked,
    format_bypass_alert,
)


def test_format_probation_start():
    msg = format_probation_start("v6", 10, "Tighten RSI threshold to 65")
    assert "v6" in msg
    assert "10" in msg
    assert "Tighten RSI threshold to 65" in msg


def test_format_promotion():
    msg = format_promotion("v6", 10, 62.0, 1.6, 55.0, 1.4)
    assert "v6" in msg
    assert "62" in msg
    assert "1.6" in msg
    assert "55" in msg
    assert "1.4" in msg


def test_format_revert():
    msg = format_revert("v6", 55.0, 1.4, 38.0, 0.7)
    assert "v6" in msg
    assert "55" in msg
    assert "38" in msg
    assert "0.7" in msg


def test_format_gate_blocked():
    msg = format_gate_blocked("Tighten RSI", 0.82, 14)
    assert "0.82" in msg
    assert "14" in msg


def test_format_bypass_alert():
    msg = format_bypass_alert("v7")
    assert "v7" in msg
```

Add to `tests/test_sessions.py`:

```python
from scheduler.sessions import build_eod_reflection_prompt, build_weekly_review_prompt


def test_eod_prompt_includes_pending_feedback():
    prompt = build_eod_reflection_prompt("2026-04-12", [], pending_feedback="v5 was reverted.")
    assert "FEEDBACK" in prompt
    assert "v5 was reverted." in prompt


def test_eod_prompt_omits_feedback_when_none():
    prompt = build_eod_reflection_prompt("2026-04-12", [])
    assert "FEEDBACK" not in prompt


def test_weekly_prompt_includes_pending_feedback():
    prompt = build_weekly_review_prompt("2026-04-12", 15, pending_feedback="Change blocked.")
    assert "FEEDBACK" in prompt
    assert "Change blocked." in prompt


def test_weekly_prompt_omits_feedback_when_none():
    prompt = build_weekly_review_prompt("2026-04-12", 15)
    assert "FEEDBACK" not in prompt
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_notifier.py::test_format_probation_start \
       tests/test_sessions.py::test_eod_prompt_includes_pending_feedback -v
```
Expected: FAIL — functions not defined, parameter not accepted.

- [ ] **Step 3: Add five formatters to notifier.py**

Add to `scheduler/notifier.py`:

```python
def format_probation_start(version: str, promote_after: int, description: str) -> str:
    return (
        f"🔬 STRATEGY {version} — PROBATIONARY\n"
        f"Change: {description}\n"
        f"Promote after: {promote_after} closed trades\n"
        f"Auto-reverts if win rate drops >15pp or avg R drops >0.5"
    )


def format_promotion(
    version: str,
    trade_count: int,
    new_win_rate: float,
    new_avg_r: float,
    baseline_win_rate: float,
    baseline_avg_r: float,
) -> str:
    return (
        f"✅ STRATEGY {version} PROMOTED → confirmed\n"
        f"Trades evaluated: {trade_count}\n"
        f"Win rate: {baseline_win_rate:.1f}% → {new_win_rate:.1f}%\n"
        f"Avg R: {baseline_avg_r:.2f} → {new_avg_r:.2f}"
    )


def format_revert(
    version: str,
    baseline_win_rate: float,
    baseline_avg_r: float,
    actual_win_rate: float,
    actual_avg_r: float,
) -> str:
    return (
        f"⏪ STRATEGY {version} REVERTED\n"
        f"Win rate: {baseline_win_rate:.1f}% → {actual_win_rate:.1f}%\n"
        f"Avg R: {baseline_avg_r:.2f} → {actual_avg_r:.2f}\n"
        f"Previous confirmed version restored."
    )


def format_gate_blocked(description: str, avg_r_blocked: float, trades_evaluated: int) -> str:
    return (
        f"🚫 STRATEGY CHANGE BLOCKED\n"
        f"Proposed: {description}\n"
        f"Pre-screen: would have removed {trades_evaluated} net-profitable trades "
        f"(avg R={avg_r_blocked:.2f})\n"
        f"Change not applied."
    )


def format_bypass_alert(version: str) -> str:
    return (
        f"⚠️ STRATEGY DOC BYPASS DETECTED\n"
        f"Claude wrote strategy_doc directly (version bumped to {version}).\n"
        f"Change captured and wrapped in probation automatically."
    )
```

- [ ] **Step 4: Update sessions.py**

Replace `build_eod_reflection_prompt` and `build_weekly_review_prompt` in `scheduler/sessions.py`. Also add `Optional` to the imports if not already present.

```python
from typing import Optional


def build_eod_reflection_prompt(
    date: str,
    trades_today: list,
    pending_feedback: Optional[str] = None,
) -> str:
    trades_json = json.dumps(trades_today)
    prompt = f"SESSION: eod_reflection | DATE: {date} | TIME: 15:45 ET | TRADES_TODAY: {trades_json}"
    if pending_feedback:
        prompt += f" | FEEDBACK: {pending_feedback}"
    return prompt


def build_weekly_review_prompt(
    date: str,
    week_number: int,
    pending_feedback: Optional[str] = None,
) -> str:
    prompt = f"SESSION: weekly_review | DATE: {date} | WEEK: {week_number}"
    if pending_feedback:
        prompt += f" | FEEDBACK: {pending_feedback}"
    return prompt
```

- [ ] **Step 5: Run all affected tests**

```
pytest tests/test_notifier.py tests/test_sessions.py -v
```
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add scheduler/notifier.py scheduler/sessions.py tests/test_notifier.py tests/test_sessions.py
git commit -m "feat: add strategy gate formatters, pending_feedback prompt injection"
```

---

### Task 4: strategy_gate.py — run_prescreen + snapshot_baseline_metrics

**Files:**
- Create: `scheduler/strategy_gate.py`
- Create: `tests/test_strategy_gate.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_strategy_gate.py`:

```python
import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import Mock

import scheduler.tools.sqlite as sqlite_mod
import scheduler.strategy_gate as gate_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trades.db")
    monkeypatch.setattr(sqlite_mod, "DB_PATH", db_path)
    monkeypatch.setattr(gate_mod, "DB_PATH", db_path)
    sqlite_mod.bootstrap_db()
    return db_path


@pytest.fixture
def feedback_path(tmp_path, monkeypatch):
    path = tmp_path / "pending_feedback.txt"
    monkeypatch.setattr(gate_mod, "PENDING_FEEDBACK_PATH", path)
    return path


def _insert_version(db_path: str, version: str = "v1", status: str = "confirmed"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, promote_after) VALUES (?, ?, 'doc', 20)",
        (version, status),
    )
    conn.commit()
    conn.close()


def _insert_closed_trade(db_path: str, r_multiple: float, outcome_pnl: float,
                          version: str = "v1", context: dict = None):
    conn = sqlite3.connect(db_path)
    ctx = json.dumps(context) if context else None
    conn.execute(
        """INSERT INTO trades
           (ticker, side, entry_price, size, setup_type, hypothesis_id, rationale,
            vix_at_entry, regime, r_multiple, outcome_pnl, strategy_version, context_json, closed_at)
           VALUES ('AAPL','buy',100,10,'momentum','H001','test',15.0,'bull',?,?,?,?,datetime('now'))""",
        (r_multiple, outcome_pnl, version, ctx),
    )
    conn.commit()
    conn.close()


# --- run_prescreen ---

def test_prescreen_blocks_when_removed_trades_are_profitable(db):
    _insert_version(db)
    # Passes filter (rsi < 65) — losing
    _insert_closed_trade(db, r_multiple=-0.5, outcome_pnl=-50, context={"rsi": 55.0})
    # Fails filter (rsi >= 65) — winning; would be removed
    _insert_closed_trade(db, r_multiple=1.5, outcome_pnl=150, context={"rsi": 70.0})

    result = gate_mod.run_prescreen("json_extract(context_json, '$.rsi') < 65")
    assert result.blocked is True
    assert result.avg_r_blocked > 0
    assert result.trades_evaluated == 1


def test_prescreen_allows_when_removed_trades_are_losing(db):
    _insert_version(db)
    _insert_closed_trade(db, r_multiple=1.5, outcome_pnl=150, context={"rsi": 55.0})
    _insert_closed_trade(db, r_multiple=-0.8, outcome_pnl=-80, context={"rsi": 70.0})

    result = gate_mod.run_prescreen("json_extract(context_json, '$.rsi') < 65")
    assert result.blocked is False
    assert result.avg_r_blocked < 0


def test_prescreen_not_blocked_when_no_context_json_data(db):
    _insert_version(db)
    # Trades without context_json excluded from pre-screen
    _insert_closed_trade(db, r_multiple=2.0, outcome_pnl=200, context=None)

    result = gate_mod.run_prescreen("json_extract(context_json, '$.rsi') < 65")
    assert result.blocked is False
    assert result.trades_evaluated == 0


def test_prescreen_raises_on_blocked_sql_keyword(db):
    with pytest.raises(gate_mod.StrategyGateError, match="blocked keyword"):
        gate_mod.run_prescreen("1=1; DROP TABLE trades")


# --- snapshot_baseline_metrics ---

def test_snapshot_returns_none_when_no_trades(db):
    _insert_version(db)
    metrics = gate_mod.snapshot_baseline_metrics()
    assert metrics["win_rate"] is None
    assert metrics["avg_r"] is None


def test_snapshot_computes_correct_metrics(db):
    _insert_version(db)
    _insert_closed_trade(db, r_multiple=2.0, outcome_pnl=200)   # win
    _insert_closed_trade(db, r_multiple=-1.0, outcome_pnl=-100)  # loss

    metrics = gate_mod.snapshot_baseline_metrics()
    assert metrics["win_rate"] == pytest.approx(50.0)
    assert metrics["avg_r"] == pytest.approx(0.5)


def test_snapshot_returns_none_when_no_strategy_version_row(db):
    # No rows in strategy_versions at all
    metrics = gate_mod.snapshot_baseline_metrics()
    assert metrics["win_rate"] is None
    assert metrics["avg_r"] is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_strategy_gate.py -v
```
Expected: FAIL — module not found.

- [ ] **Step 3: Create scheduler/strategy_gate.py with run_prescreen and snapshot**

```python
"""
Strategy change evaluation gate.

Handles probationary versioning for every strategy change Claude proposes:
  - Pre-screen backtestable (filter_sql) changes against 60 days of closed trades
  - Apply approved changes as probationary versions in strategy_versions table
  - Auto-promote or auto-revert after minimum trade count
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from scheduler.tools.sqlite import _connect, DB_PATH, _BLOCKED_KEYWORDS

log = logging.getLogger(__name__)

PENDING_FEEDBACK_PATH = Path("/app/state/pending_feedback.txt")
BACKTEST_DAYS = 60
PROMOTE_AFTER_BACKTESTED = 10
PROMOTE_AFTER_QUALITATIVE = 20
REVERT_WIN_RATE_DROP_THRESHOLD = 15.0  # percentage points
REVERT_AVG_R_DROP_THRESHOLD = 0.5


class StrategyGateError(Exception):
    """Raised when a proposed change is blocked by the evaluation gate.

    avg_r_blocked and trades_evaluated are set only for pre-screen blocks.
    They are None for one-at-a-time guard rejections.
    """
    def __init__(
        self,
        message: str,
        avg_r_blocked: Optional[float] = None,
        trades_evaluated: Optional[int] = None,
    ):
        super().__init__(message)
        self.avg_r_blocked = avg_r_blocked
        self.trades_evaluated = trades_evaluated


@dataclass
class PreScreenResult:
    blocked: bool
    avg_r_blocked: Optional[float]
    trades_evaluated: int


def _append_feedback(message: str) -> None:
    """Append a message to the pending feedback file. Always append — never overwrite."""
    PENDING_FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PENDING_FEEDBACK_PATH, "a") as f:
        f.write(message.strip() + "\n")


def run_prescreen(filter_sql: str) -> PreScreenResult:
    """
    Pre-screen a backtestable filter against the last 60 days of closed trades.

    filter_sql is the condition Claude wants to KEEP (e.g. 'setup_type = "momentum"').
    The pre-screen queries the COMPLEMENT — trades that would be REMOVED.
    Trades with NULL context_json are excluded to avoid bias from pre-feature records.

    Returns PreScreenResult(blocked=True) if avg_r of removed trades > 0 (net profitable).
    Raises StrategyGateError if filter_sql contains a blocked SQL keyword.
    """
    upper = filter_sql.upper()
    for kw in _BLOCKED_KEYWORDS:
        if kw in upper:
            raise StrategyGateError(
                f"filter_sql contains blocked keyword '{kw}' — cannot pre-screen"
            )

    query = f"""
        SELECT
            AVG(r_multiple) AS avg_r_blocked,
            COUNT(*)        AS n
        FROM trades
        WHERE NOT ({filter_sql})
          AND context_json IS NOT NULL
          AND closed_at > datetime('now', '-{BACKTEST_DAYS} days')
          AND closed_at IS NOT NULL
    """
    conn = _connect(read_only=True)
    try:
        row = conn.execute(query).fetchone()
        n = row["n"] if row else 0
        avg_r = row["avg_r_blocked"] if row else None
        if n == 0 or avg_r is None:
            return PreScreenResult(blocked=False, avg_r_blocked=None, trades_evaluated=0)
        return PreScreenResult(blocked=avg_r > 0, avg_r_blocked=avg_r, trades_evaluated=n)
    finally:
        conn.close()


def snapshot_baseline_metrics() -> dict:
    """
    Return win_rate (0–100) and avg_r for the current active strategy version's closed trades.
    Returns {"win_rate": None, "avg_r": None} if no active version or no closed trades.
    """
    conn = _connect(read_only=True)
    try:
        version_row = conn.execute(
            "SELECT version FROM strategy_versions "
            "WHERE status IN ('confirmed', 'probationary') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if version_row is None:
            return {"win_rate": None, "avg_r": None}

        metrics = conn.execute(
            """
            SELECT
                AVG(CASE WHEN outcome_pnl > 0 THEN 100.0 ELSE 0.0 END) AS win_rate,
                AVG(r_multiple)                                          AS avg_r
            FROM trades
            WHERE strategy_version = ? AND closed_at IS NOT NULL
            """,
            (version_row["version"],),
        ).fetchone()

        return {"win_rate": metrics["win_rate"], "avg_r": metrics["avg_r"]}
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_strategy_gate.py -k "prescreen or snapshot" -v
```
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/strategy_gate.py tests/test_strategy_gate.py
git commit -m "feat: strategy_gate module with run_prescreen and snapshot_baseline_metrics"
```

---

### Task 5: strategy_gate.py — apply_change

**Files:**
- Modify: `scheduler/strategy_gate.py`
- Modify: `tests/test_strategy_gate.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_strategy_gate.py`:

```python
# --- apply_change ---

def test_apply_change_raises_when_probationary_already_active(db, feedback_path):
    _insert_version(db, "v1", "confirmed")
    _insert_version(db, "v2", "probationary")

    mock_agent = Mock()
    with pytest.raises(gate_mod.StrategyGateError, match="probationary"):
        gate_mod.apply_change(mock_agent, {"description": "new change", "new_strategy_doc": "doc"})

    assert feedback_path.exists()
    assert "v2" in feedback_path.read_text()


def test_apply_change_raises_when_prescreen_blocks(db, feedback_path):
    _insert_version(db, "v1", "confirmed")
    # Profitable trade that would be removed by the filter
    _insert_closed_trade(db, r_multiple=2.0, outcome_pnl=200, context={"rsi": 70.0})

    mock_agent = Mock()
    with pytest.raises(gate_mod.StrategyGateError, match="blocked"):
        gate_mod.apply_change(
            mock_agent,
            {
                "description": "Tighten RSI",
                "new_strategy_doc": "doc",
                "filter_sql": "json_extract(context_json, '$.rsi') < 65",
            },
        )
    mock_agent.update_memory_block.assert_not_called()


def test_apply_change_qualitative_sets_promote_after_20(db):
    _insert_version(db, "v1", "confirmed")
    mock_agent = Mock()

    result = gate_mod.apply_change(mock_agent, {"description": "Be patient", "new_strategy_doc": "new doc"})

    assert result["version"] == "v2"
    assert result["promote_after"] == 20
    assert result["description"] == "Be patient"


def test_apply_change_backtested_sets_promote_after_10(db):
    _insert_version(db, "v1", "confirmed")
    # Only losing trade removed — pre-screen passes
    _insert_closed_trade(db, r_multiple=-1.0, outcome_pnl=-100, context={"rsi": 70.0})

    mock_agent = Mock()
    result = gate_mod.apply_change(
        mock_agent,
        {
            "description": "Tighten RSI",
            "new_strategy_doc": "doc",
            "filter_sql": "json_extract(context_json, '$.rsi') < 65",
        },
    )
    assert result["promote_after"] == 10


def test_apply_change_increments_version_number(db):
    _insert_version(db, "v3", "confirmed")
    mock_agent = Mock()
    result = gate_mod.apply_change(mock_agent, {"description": "x", "new_strategy_doc": "d"})
    assert result["version"] == "v4"


def test_apply_change_writes_letta_before_inserting_db_row(db):
    _insert_version(db, "v1", "confirmed")
    mock_agent = Mock()
    mock_agent.update_memory_block.side_effect = RuntimeError("Letta unavailable")

    with pytest.raises(RuntimeError):
        gate_mod.apply_change(mock_agent, {"description": "x", "new_strategy_doc": "d"})

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT * FROM strategy_versions WHERE version='v2'").fetchone()
    conn.close()
    assert row is None  # DB row not inserted


def test_apply_change_strategy_doc_contains_metadata_block(db):
    _insert_version(db, "v1", "confirmed")
    mock_agent = Mock()

    gate_mod.apply_change(mock_agent, {"description": "x", "new_strategy_doc": "the body"})

    written_doc = mock_agent.update_memory_block.call_args[0][1]
    assert "version: v2" in written_doc
    assert "status: probationary" in written_doc
    assert "the body" in written_doc


def test_apply_change_inserts_probationary_row_in_db(db):
    _insert_version(db, "v1", "confirmed")
    mock_agent = Mock()
    gate_mod.apply_change(mock_agent, {"description": "x", "new_strategy_doc": "body"})

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM strategy_versions WHERE version='v2'").fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "probationary"
    assert "body" in row["doc_text"]
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_strategy_gate.py -k "apply_change" -v
```
Expected: FAIL — `apply_change` not defined.

- [ ] **Step 3: Add apply_change to scheduler/strategy_gate.py**

```python
def apply_change(agent, proposed_change: dict) -> dict:
    """
    Apply a proposed strategy change as a new probationary version.

    Raises StrategyGateError if:
      - A probationary version is already active (one-at-a-time guard)
      - The pre-screen blocks the change (avg_r of removed trades > 0)

    Writes Letta core memory BEFORE inserting the strategy_versions row so that
    a failed Letta write leaves no phantom DB row.

    Returns {"version": str, "promote_after": int, "description": str}.
    """
    description = proposed_change.get("description", "")
    new_doc = proposed_change.get("new_strategy_doc", "")
    filter_sql = proposed_change.get("filter_sql")

    conn = _connect()
    try:
        # One-at-a-time guard
        prob_row = conn.execute(
            "SELECT sv.version, sv.promote_after, "
            "  (SELECT COUNT(*) FROM trades t "
            "   WHERE t.strategy_version = sv.version AND t.closed_at IS NOT NULL) AS trade_count "
            "FROM strategy_versions sv "
            "WHERE sv.status = 'probationary' "
            "ORDER BY sv.created_at DESC LIMIT 1"
        ).fetchone()
        if prob_row:
            msg = (
                f"Strategy {prob_row['version']} is still probationary "
                f"({prob_row['trade_count']}/{prob_row['promote_after']} trades). "
                f"No further strategy changes until it is promoted or reverted."
            )
            _append_feedback(msg)
            raise StrategyGateError(msg)

        # Determine next version number
        last_row = conn.execute(
            "SELECT version FROM strategy_versions ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if last_row:
            try:
                new_num = int(last_row["version"].lstrip("v")) + 1
            except ValueError:
                new_num = 2
            new_version = f"v{new_num}"
        else:
            new_version = "v2"
    finally:
        conn.close()

    # Snapshot baseline metrics before pre-screen (avoids extra DB round-trip)
    baseline = snapshot_baseline_metrics()
    wr_str = f"{baseline['win_rate']:.1f}" if baseline["win_rate"] is not None else "null"
    ar_str = f"{baseline['avg_r']:.2f}" if baseline["avg_r"] is not None else "null"

    # Pre-screen (backtestable path only)
    promote_after = PROMOTE_AFTER_QUALITATIVE
    if filter_sql:
        try:
            result = run_prescreen(filter_sql)
            if result.blocked:
                msg = (
                    f"Strategy change blocked by pre-screen: '{description}'. "
                    f"The filter would have removed {result.trades_evaluated} net-profitable trades "
                    f"(avg R={result.avg_r_blocked:.2f}). Change not applied."
                )
                _append_feedback(msg)
                raise StrategyGateError(
                    msg,
                    avg_r_blocked=result.avg_r_blocked,
                    trades_evaluated=result.trades_evaluated,
                )
            promote_after = PROMOTE_AFTER_BACKTESTED
        except StrategyGateError:
            raise
        except Exception as exc:
            # Malformed filter_sql — fall back to qualitative, don't block
            log.warning("filter_sql pre-screen failed (%s), treating as qualitative", exc)
            promote_after = PROMOTE_AFTER_QUALITATIVE

    # Build final doc: scheduler-owned metadata block prepended to Claude's proposed text
    metadata = (
        f"## Version metadata\n"
        f"version: {new_version}\n"
        f"status: probationary\n"
        f"promote_after: {promote_after}\n"
        f"baseline_win_rate: {wr_str}\n"
        f"baseline_avg_r: {ar_str}\n\n"
    )
    final_doc = metadata + new_doc

    # Write Letta first — if this raises, the DB row is NOT inserted
    agent.update_memory_block("strategy_doc", final_doc)

    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO strategy_versions
                (version, status, doc_text, baseline_win_rate, baseline_avg_r, promote_after)
            VALUES (?, 'probationary', ?, ?, ?, ?)
            """,
            (new_version, final_doc, baseline["win_rate"], baseline["avg_r"], promote_after),
        )
        conn.commit()
    finally:
        conn.close()

    return {"version": new_version, "promote_after": promote_after, "description": description}
```

- [ ] **Step 4: Run tests**

```
pytest tests/test_strategy_gate.py -k "apply_change" -v
```
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/strategy_gate.py tests/test_strategy_gate.py
git commit -m "feat: add apply_change to strategy_gate"
```

---

### Task 6: strategy_gate.py — check_probation

**Files:**
- Modify: `scheduler/strategy_gate.py`
- Modify: `tests/test_strategy_gate.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_strategy_gate.py`:

```python
# --- check_probation ---

def test_check_probation_returns_none_with_no_probationary_version(db):
    _insert_version(db, "v1", "confirmed")
    assert gate_mod.check_probation(Mock()) is None


def test_check_probation_returns_none_when_not_enough_trades(db):
    _insert_version(db, "v1", "confirmed")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, "
        "baseline_win_rate, baseline_avg_r, promote_after) "
        "VALUES ('v2', 'probationary', 'doc', 55.0, 1.4, 10)"
    )
    conn.commit()
    conn.close()
    for _ in range(5):
        _insert_closed_trade(db, r_multiple=1.0, outcome_pnl=100, version="v2")

    assert gate_mod.check_probation(Mock()) is None


def test_check_probation_promotes_when_performance_holds(db):
    _insert_version(db, "v1", "confirmed")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, "
        "baseline_win_rate, baseline_avg_r, promote_after) "
        "VALUES ('v2', 'probationary', "
        "'## Version metadata\nversion: v2\nstatus: probationary\n\nbody', 55.0, 1.4, 5)"
    )
    conn.commit()
    conn.close()
    # 5 trades: 4 wins (80% win rate, avg_r ≈ 1.34) — no degradation
    for i in range(5):
        _insert_closed_trade(db, r_multiple=1.5 if i < 4 else -0.5, outcome_pnl=150 if i < 4 else -50, version="v2")

    mock_agent = Mock()
    mock_agent.get_memory_block.return_value = (
        "## Version metadata\nversion: v2\nstatus: probationary\npromote_after: 5\n\nbody"
    )
    result = gate_mod.check_probation(mock_agent)

    assert result["outcome"] == "promoted"
    assert result["version"] == "v2"
    mock_agent.update_memory_block.assert_called_once()
    written = mock_agent.update_memory_block.call_args[0][1]
    assert "status: confirmed" in written

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM strategy_versions WHERE version='v2'").fetchone()
    conn.close()
    assert row[0] == "confirmed"


def test_check_probation_reverts_on_win_rate_degradation(db, feedback_path):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
        "VALUES ('v1', 'confirmed', 'v1 confirmed doc', 20)"
    )
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, "
        "baseline_win_rate, baseline_avg_r, promote_after) "
        "VALUES ('v2', 'probationary', 'v2 doc', 55.0, 1.4, 5)"
    )
    conn.commit()
    conn.close()
    # 5 trades: 1 win, 4 losses → 20% win rate (drops 35pp from baseline 55%)
    for i in range(5):
        _insert_closed_trade(db, r_multiple=1.5 if i == 0 else -1.0,
                              outcome_pnl=150 if i == 0 else -100, version="v2")

    mock_agent = Mock()
    result = gate_mod.check_probation(mock_agent)

    assert result["outcome"] == "reverted"
    assert result["version"] == "v2"
    mock_agent.update_memory_block.assert_called_once_with("strategy_doc", "v1 confirmed doc")

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, revert_reason FROM strategy_versions WHERE version='v2'").fetchone()
    conn.close()
    assert row[0] == "reverted"
    assert "win rate" in row[1]
    assert feedback_path.read_text().strip() != ""


def test_check_probation_reverts_on_avg_r_degradation(db, feedback_path):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
        "VALUES ('v1', 'confirmed', 'v1 doc', 20)"
    )
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, "
        "baseline_win_rate, baseline_avg_r, promote_after) "
        "VALUES ('v2', 'probationary', 'v2 doc', 55.0, 1.4, 5)"
    )
    conn.commit()
    conn.close()
    # 5 trades, avg_r = 0.7 — drops 0.7 > threshold 0.5
    for _ in range(5):
        _insert_closed_trade(db, r_multiple=0.7, outcome_pnl=70, version="v2")

    mock_agent = Mock()
    result = gate_mod.check_probation(mock_agent)
    assert result["outcome"] == "reverted"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_strategy_gate.py -k "check_probation" -v
```
Expected: FAIL — `check_probation` not defined.

- [ ] **Step 3: Add check_probation to scheduler/strategy_gate.py**

```python
def check_probation(agent) -> Optional[dict]:
    """
    Evaluate the active probationary strategy version if it has enough closed trades.

    Returns None if no probationary version or trade count < promote_after.

    Returns a dict on promotion or reversion:
      {outcome, version, trade_count, new_win_rate, new_avg_r,
       baseline_win_rate, baseline_avg_r, revert_reason}
    """
    conn = _connect()
    try:
        prob_row = conn.execute(
            "SELECT version, baseline_win_rate, baseline_avg_r, promote_after "
            "FROM strategy_versions WHERE status = 'probationary' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if prob_row is None:
            return None

        version = prob_row["version"]
        baseline_wr = prob_row["baseline_win_rate"]
        baseline_ar = prob_row["baseline_avg_r"]
        promote_after = prob_row["promote_after"]

        trade_count = conn.execute(
            "SELECT COUNT(*) AS n FROM trades "
            "WHERE strategy_version = ? AND closed_at IS NOT NULL",
            (version,),
        ).fetchone()["n"]

        if trade_count < promote_after:
            return None

        metrics = conn.execute(
            """
            SELECT
                AVG(CASE WHEN outcome_pnl > 0 THEN 100.0 ELSE 0.0 END) AS win_rate,
                AVG(r_multiple)                                          AS avg_r
            FROM trades
            WHERE strategy_version = ? AND closed_at IS NOT NULL
            """,
            (version,),
        ).fetchone()
        new_wr = metrics["win_rate"]
        new_ar = metrics["avg_r"]

        wr_degraded = (
            baseline_wr is not None and new_wr is not None
            and (baseline_wr - new_wr) > REVERT_WIN_RATE_DROP_THRESHOLD
        )
        ar_degraded = (
            baseline_ar is not None and new_ar is not None
            and (baseline_ar - new_ar) > REVERT_AVG_R_DROP_THRESHOLD
        )

        if not wr_degraded and not ar_degraded:
            conn.execute(
                "UPDATE strategy_versions SET status='confirmed', resolved_at=datetime('now') "
                "WHERE version=?",
                (version,),
            )
            conn.commit()

            current_doc = agent.get_memory_block("strategy_doc") or ""
            updated_doc = current_doc.replace("status: probationary", "status: confirmed")
            agent.update_memory_block("strategy_doc", updated_doc)

            return {
                "outcome": "promoted", "version": version, "trade_count": trade_count,
                "new_win_rate": new_wr, "new_avg_r": new_ar,
                "baseline_win_rate": baseline_wr, "baseline_avg_r": baseline_ar,
                "revert_reason": None,
            }
        else:
            reasons = []
            if wr_degraded:
                reasons.append(f"win rate dropped {baseline_wr:.1f}% → {new_wr:.1f}%")
            if ar_degraded:
                reasons.append(f"avg R dropped {baseline_ar:.2f} → {new_ar:.2f}")
            revert_reason = "; ".join(reasons)

            conn.execute(
                "UPDATE strategy_versions SET status='reverted', resolved_at=datetime('now'), "
                "revert_reason=? WHERE version=?",
                (revert_reason, version),
            )
            conn.commit()

            confirmed_row = conn.execute(
                "SELECT doc_text FROM strategy_versions "
                "WHERE status='confirmed' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if confirmed_row:
                agent.update_memory_block("strategy_doc", confirmed_row["doc_text"])

            _append_feedback(
                f"Strategy {version} was automatically reverted after {trade_count} trades. "
                f"{revert_reason.capitalize()}. Previous confirmed version restored."
            )

            return {
                "outcome": "reverted", "version": version, "trade_count": trade_count,
                "new_win_rate": new_wr, "new_avg_r": new_ar,
                "baseline_win_rate": baseline_wr, "baseline_avg_r": baseline_ar,
                "revert_reason": revert_reason,
            }
    finally:
        conn.close()
```

- [ ] **Step 4: Run all strategy_gate tests**

```
pytest tests/test_strategy_gate.py -v
```
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/strategy_gate.py tests/test_strategy_gate.py
git commit -m "feat: add check_probation to strategy_gate"
```

---

### Task 7: bootstrap.py — v1 seed row

**Files:**
- Modify: `scheduler/bootstrap.py`

The v1 seed row is inserted only on first bootstrap (inside the `create_new` path, not the early-return path). It uses `INITIAL_STRATEGY_DOC` from `agent.py` — same text written to core memory — ensuring the DB row and core memory start identical.

- [ ] **Step 1: Write failing test**

Create `tests/test_bootstrap.py`:

```python
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import scheduler.tools.sqlite as sqlite_mod


@pytest.fixture
def fresh_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trades.db")
    agent_id_path = tmp_path / ".agent_id"
    monkeypatch.setattr(sqlite_mod, "DB_PATH", db_path)
    monkeypatch.setenv("LETTA_AGENT_NAME", "test_trader")
    monkeypatch.setenv("LETTA_SERVER_URL", "http://localhost:8283")
    sqlite_mod.bootstrap_db()
    return {"db_path": db_path, "agent_id_path": agent_id_path, "tmp_path": tmp_path}


def test_bootstrap_seeds_v1_row_in_strategy_versions(fresh_env, monkeypatch):
    from scheduler.agent import INITIAL_STRATEGY_DOC

    mock_agent = MagicMock()
    mock_agent.agent_id = "mock-agent-123"

    monkeypatch.setattr("scheduler.bootstrap.AGENT_ID_FILE", fresh_env["agent_id_path"])
    monkeypatch.setattr("scheduler.bootstrap.LettaTraderAgent.create_new",
                        staticmethod(lambda *a, **kw: mock_agent))
    monkeypatch.setattr("scheduler.bootstrap.register_all_tools", lambda *a: [])
    monkeypatch.setattr("scheduler.bootstrap.attach_alpaca_mcp", lambda *a: False)

    from scheduler.bootstrap import bootstrap
    bootstrap()

    conn = sqlite3.connect(fresh_env["db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM strategy_versions WHERE version='v1'").fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "confirmed"
    assert INITIAL_STRATEGY_DOC in row["doc_text"]
    assert "## Version metadata" in row["doc_text"]
    assert "version: v1" in row["doc_text"]
```

- [ ] **Step 2: Run test to confirm it fails**

```
pytest tests/test_bootstrap.py::test_bootstrap_seeds_v1_row_in_strategy_versions -v
```
Expected: FAIL — no v1 row inserted.

- [ ] **Step 3: Add v1 seed to bootstrap.py**

In `scheduler/bootstrap.py`, after `attach_alpaca_mcp` and before saving the agent ID file, add:

```python
    # Seed v1 row in strategy_versions using the same doc text written to core memory
    from scheduler.tools.sqlite import _connect as _db_connect
    from scheduler.agent import INITIAL_STRATEGY_DOC
    db_conn = _db_connect()
    try:
        existing = db_conn.execute(
            "SELECT version FROM strategy_versions WHERE version='v1'"
        ).fetchone()
        if existing is None:
            db_conn.execute(
                "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
                "VALUES ('v1', 'confirmed', ?, 20)",
                (INITIAL_STRATEGY_DOC,),
            )
            db_conn.commit()
            print("Seeded strategy_versions with v1 (confirmed).")
    finally:
        db_conn.close()
```

The full `bootstrap()` function's new-agent path (after the early-return block) now ends:

```python
    agent = LettaTraderAgent.create_new(agent_name)
    print(f"Agent created: {agent.agent_id}")

    print("Registering tools...")
    tools = register_all_tools(agent.agent_id)
    print(f"Registered: {tools}")

    print("Attaching Alpaca MCP server...")
    ok = attach_alpaca_mcp(agent.agent_id)
    print(f"Alpaca MCP attached: {ok}")

    # Seed strategy_versions v1 row — metadata block prepended for consistency with all later versions
    from scheduler.tools.sqlite import _connect as _db_connect
    from scheduler.agent import INITIAL_STRATEGY_DOC
    _v1_metadata = (
        "## Version metadata\n"
        "version: v1\n"
        "status: confirmed\n"
        "promote_after: 20\n"
        "baseline_win_rate: null\n"
        "baseline_avg_r: null\n\n"
    )
    _v1_doc_text = _v1_metadata + INITIAL_STRATEGY_DOC
    db_conn = _db_connect()
    try:
        if db_conn.execute("SELECT version FROM strategy_versions WHERE version='v1'").fetchone() is None:
            db_conn.execute(
                "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
                "VALUES ('v1', 'confirmed', ?, 20)",
                (_v1_doc_text,),
            )
            db_conn.commit()
            print("Seeded strategy_versions with v1 (confirmed).")
    finally:
        db_conn.close()

    # Load script library index into agent memory
    index_path = Path("/app/scripts/indicators/index.json")
    if index_path.exists():
        index_content = index_path.read_text()
        agent.send_session(
            f"BOOTSTRAP: Load this indicator library index into your memory for future reference.\n\n{index_content}"
        )
        print("Indicator library loaded.")

    AGENT_ID_FILE.write_text(agent.agent_id)
    print(f"Bootstrap complete. Agent ID saved to {AGENT_ID_FILE}")
```

- [ ] **Step 4: Run test**

```
pytest tests/test_bootstrap.py -v
```
Expected: All pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/bootstrap.py tests/test_bootstrap.py
git commit -m "feat: seed strategy_versions v1 row on first bootstrap"
```

---

### Task 8: main.py — integration

**Files:**
- Modify: `scheduler/main.py`
- Modify: `tests/test_main.py` (create if it doesn't exist)

This task wires everything together in `run_session`:
1. Before EOD/weekly session: read `strategy_doc` for fallback detection
2. After session: check for `proposed_change` in JSON output → call `apply_change`
3. Fallback: version changed with no `proposed_change` → wrap in probation + Telegram alert
4. Always after EOD/weekly: call `check_probation` → Telegram on promote/revert
5. Before building EOD/weekly prompts: read + clear `pending_feedback.txt`

- [ ] **Step 1: Write failing tests**

Create `tests/test_main.py`:

```python
import pytest
from unittest.mock import MagicMock, Mock, patch, call
from pathlib import Path


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.send_session.return_value = '{"status": "ok"}'
    agent.get_memory_block.return_value = "## Version metadata\nversion: v1\nstatus: confirmed\n\nbody"
    return agent


def test_run_session_calls_apply_change_on_proposed_change(mock_agent, tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler.main.AGENT_ID_FILE", tmp_path / ".agent_id")
    (tmp_path / ".agent_id").write_text("agent-1")
    monkeypatch.setattr("scheduler.main.get_agent", lambda: mock_agent)

    mock_agent.send_session.return_value = (
        '{"status": "ok", "proposed_change": {"description": "test change", '
        '"new_strategy_doc": "new doc"}}'
    )

    apply_result = {"version": "v2", "promote_after": 20, "description": "test change"}
    with patch("scheduler.main.strategy_gate") as mock_gate:
        mock_gate.apply_change.return_value = apply_result
        mock_gate.check_probation.return_value = None
        with patch("scheduler.main.send_telegram") as mock_tg:
            from scheduler.main import run_session
            run_session("eod_reflection", "prompt")

    mock_gate.apply_change.assert_called_once()
    mock_tg.assert_called()


def test_run_session_calls_check_probation_after_eod(mock_agent, tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler.main.AGENT_ID_FILE", tmp_path / ".agent_id")
    (tmp_path / ".agent_id").write_text("agent-1")
    monkeypatch.setattr("scheduler.main.get_agent", lambda: mock_agent)

    with patch("scheduler.main.strategy_gate") as mock_gate:
        mock_gate.apply_change.return_value = None
        mock_gate.check_probation.return_value = None
        from scheduler.main import run_session
        run_session("eod_reflection", "prompt")

    mock_gate.check_probation.assert_called_once()


def test_run_session_does_not_call_gate_on_pre_market(mock_agent, tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler.main.AGENT_ID_FILE", tmp_path / ".agent_id")
    (tmp_path / ".agent_id").write_text("agent-1")
    monkeypatch.setattr("scheduler.main.get_agent", lambda: mock_agent)

    with patch("scheduler.main.strategy_gate") as mock_gate:
        from scheduler.main import run_session
        run_session("pre_market", "prompt")

    mock_gate.apply_change.assert_not_called()
    mock_gate.check_probation.assert_not_called()


def test_read_and_clear_pending_feedback_returns_content_and_deletes(tmp_path, monkeypatch):
    feedback_file = tmp_path / "pending_feedback.txt"
    feedback_file.write_text("v5 reverted.\nChange blocked.\n")
    monkeypatch.setattr("scheduler.main.PENDING_FEEDBACK_PATH", feedback_file)

    from scheduler.main import _read_and_clear_pending_feedback
    result = _read_and_clear_pending_feedback()

    assert "v5 reverted" in result
    assert "Change blocked" in result
    assert not feedback_file.exists()


def test_read_and_clear_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler.main.PENDING_FEEDBACK_PATH", tmp_path / "no_such_file.txt")
    from scheduler.main import _read_and_clear_pending_feedback
    assert _read_and_clear_pending_feedback() is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```
pytest tests/test_main.py -v
```
Expected: FAIL — `strategy_gate` not imported, helper not defined.

- [ ] **Step 3: Add imports to main.py**

Add at the top of `scheduler/main.py`:

```python
import re
from pathlib import Path
from scheduler import strategy_gate
from scheduler.notifier import (
    format_probation_start,
    format_promotion,
    format_revert,
    format_gate_blocked,
    format_bypass_alert,
)

PENDING_FEEDBACK_PATH = Path("/app/state/pending_feedback.txt")
```

- [ ] **Step 4: Add _read_and_clear_pending_feedback helper to main.py**

```python
def _read_and_clear_pending_feedback() -> Optional[str]:
    """Read and delete the pending feedback file. Returns None if it doesn't exist."""
    if not PENDING_FEEDBACK_PATH.exists():
        return None
    text = PENDING_FEEDBACK_PATH.read_text().strip()
    PENDING_FEEDBACK_PATH.unlink()
    return text or None
```

Add `Optional` to the import at the top: `from typing import Optional`.

- [ ] **Step 5: Add _extract_version_from_doc helper to main.py**

```python
def _extract_strategy_version(doc_text: str) -> Optional[str]:
    """Extract the version field from a strategy doc metadata block."""
    match = re.search(r'^version:\s*(\S+)', doc_text, re.MULTILINE)
    return match.group(1) if match else None
```

- [ ] **Step 6: Update run_session with strategy gate logic**

Replace `run_session` in `scheduler/main.py`:

```python
def run_session(session_type: str, prompt: str, max_retries: int = 1):
    """Dispatch a session to the Letta agent with error handling and Telegram notifications."""
    is_strategy_session = session_type in ("eod_reflection", "weekly_review")

    # Snapshot strategy_doc before session for fallback bypass detection
    pre_doc: Optional[str] = None
    if is_strategy_session:
        try:
            pre_doc = get_agent().get_memory_block("strategy_doc")
        except Exception:
            pass

    for attempt in range(max_retries + 1):
        try:
            log.info(f"Starting session: {session_type} (attempt {attempt + 1})")
            agent = get_agent()
            raw_output = agent.send_session(prompt)
            output = parse_session_output(raw_output)

            # --- Strategy gate ---
            if is_strategy_session:
                proposed = output.get("proposed_change")
                if proposed:
                    try:
                        result = strategy_gate.apply_change(agent, proposed)
                        send_telegram(format_probation_start(
                            result["version"], result["promote_after"], result["description"]
                        ))
                    except strategy_gate.StrategyGateError as e:
                        log.info(f"Strategy gate blocked change: {e}")
                        if e.avg_r_blocked is not None:
                            # Pre-screen block — send Telegram with numbers
                            send_telegram(format_gate_blocked(
                                proposed.get("description", ""),
                                e.avg_r_blocked,
                                e.trades_evaluated or 0,
                            ))
                        # Guard rejections: feedback already written to pending_feedback.txt
                else:
                    # Fallback: detect direct write by version mismatch
                    post_doc = agent.get_memory_block("strategy_doc") or ""
                    pre_version = _extract_strategy_version(pre_doc or "")
                    post_version = _extract_strategy_version(post_doc)
                    if post_version and post_version != pre_version:
                        try:
                            strategy_gate.apply_change(
                                agent,
                                {"description": "direct write detected", "new_strategy_doc": post_doc},
                            )
                            send_telegram(format_bypass_alert(post_version))
                        except strategy_gate.StrategyGateError as e:
                            log.warning(f"Fallback wrap failed: {e}")

                # Probation check — runs after every EOD/weekly regardless of proposed_change
                probation_result = strategy_gate.check_probation(agent)
                if probation_result:
                    if probation_result["outcome"] == "promoted":
                        send_telegram(format_promotion(
                            probation_result["version"],
                            probation_result["trade_count"],
                            probation_result["new_win_rate"] or 0,
                            probation_result["new_avg_r"] or 0,
                            probation_result["baseline_win_rate"] or 0,
                            probation_result["baseline_avg_r"] or 0,
                        ))
                    else:
                        send_telegram(format_revert(
                            probation_result["version"],
                            probation_result["baseline_win_rate"] or 0,
                            probation_result["baseline_avg_r"] or 0,
                            probation_result["new_win_rate"] or 0,
                            probation_result["new_avg_r"] or 0,
                        ))

            # --- Existing notification logic ---
            if session_type == "market_open" and output.get("trades"):
                for trade in output["trades"]:
                    send_telegram(format_trade_notification(trade))

            elif session_type == "health_check" and output.get("alerts"):
                for alert in output["alerts"]:
                    send_telegram(format_alert(alert))

            elif session_type == "eod_reflection" and output:
                send_telegram(format_eod_summary(output))

            elif session_type == "weekly_review" and output:
                send_telegram(f"📅 WEEKLY REVIEW COMPLETE\n{output.get('summary', '')}")

            log_path = Path(f"/app/logs/sessions/{date.today().isoformat()}_{session_type}.json")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps({"prompt": prompt, "output": raw_output, "parsed": output}, indent=2))

            log.info(f"Session {session_type} complete.")
            return

        except Exception as e:
            log.error(f"Session {session_type} failed (attempt {attempt + 1}): {e}")
            if attempt < max_retries:
                log.info(f"Retrying in 60s...")
                time.sleep(60)
            else:
                send_telegram(format_error_notification(session_type, str(e)))
```

- [ ] **Step 7: Update job_eod_reflection and job_weekly_review to inject pending feedback**

Replace these two functions:

```python
def job_eod_reflection():
    now = datetime.now(ET)
    trades = _get_todays_trades()
    pending_feedback = _read_and_clear_pending_feedback()
    prompt = build_eod_reflection_prompt(
        date=now.strftime("%Y-%m-%d"),
        trades_today=trades,
        pending_feedback=pending_feedback,
    )
    run_session("eod_reflection", prompt)


def job_weekly_review():
    now = datetime.now(ET)
    week_num = now.isocalendar()[1]
    pending_feedback = _read_and_clear_pending_feedback()
    prompt = build_weekly_review_prompt(
        date=now.strftime("%Y-%m-%d"),
        week_number=week_num,
        pending_feedback=pending_feedback,
    )
    run_session("weekly_review", prompt)
```

- [ ] **Step 8: Run all tests**

```
pytest -v
```
Expected: All pass.

- [ ] **Step 9: Commit**

```bash
git add scheduler/main.py tests/test_main.py
git commit -m "feat: wire strategy gate into run_session — proposed_change intercept, fallback, probation check"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Covered by |
|---|---|
| Scheduler intercepts `proposed_change` from JSON | Task 8 `run_session` |
| Backtest pre-screen against last 60 days | Task 4 `run_prescreen` |
| Block condition: avg_r of removed trades > 0 | Task 4 `run_prescreen` |
| Exclude NULL context_json from pre-screen | Task 4 `run_prescreen` query |
| All changes go probationary | Task 5 `apply_change` |
| `promote_after` = 10 for backtested, 20 for qualitative | Task 5 `apply_change` |
| Snapshot baseline at change time | Task 5 `apply_change` via `snapshot_baseline_metrics` |
| `strategy_version` stamped on `trade_open` | Task 1 `trade_open` |
| `context_json` parameter on `trade_open` | Task 1 `trade_open` |
| `strategy_versions` table | Task 1 schema |
| Auto-promote after N trades | Task 6 `check_probation` |
| Auto-revert if win rate drops > 15pp | Task 6 `check_probation` |
| Auto-revert if avg R drops > 0.5 | Task 6 `check_probation` |
| Revert restores last confirmed `doc_text` | Task 6 `check_probation` |
| Feedback written on revert | Task 6 `_append_feedback` |
| One-probation-at-a-time guard | Task 5 `apply_change` guard block |
| Feedback appended (not overwritten) | Task 4 `_append_feedback` (append mode) |
| Read-and-clear pending feedback before session | Task 8 `_read_and_clear_pending_feedback` |
| Feedback injected into next session prompt | Task 3 `sessions.py`, Task 8 `job_eod_reflection` |
| Fallback: detect direct write, wrap in probation | Task 8 `run_session` fallback block |
| Fallback sends Telegram bypass alert | Task 8 `format_bypass_alert` call |
| Letta write before DB insert in `apply_change` | Task 5 step 3 — Letta write first |
| `update_memory_block` on `LettaTraderAgent` | Task 2 |
| Strategy change protocol in `INITIAL_STRATEGY_DOC` | Task 2 |
| v1 seed row in `strategy_versions` on bootstrap | Task 7 |
| Telegram notifications for all outcomes | Tasks 3 + 8 |

**No gaps found.**

**Type consistency check:** `apply_change` returns `{"version": str, "promote_after": int, "description": str}` — used as `result["version"]`, `result["promote_after"]`, `result["description"]` in Task 8. `check_probation` returns `{"outcome", "version", "trade_count", "new_win_rate", "new_avg_r", "baseline_win_rate", "baseline_avg_r", "revert_reason"}` — all fields accessed by key in Task 8 match. ✓

**No placeholder scan issues found.**
