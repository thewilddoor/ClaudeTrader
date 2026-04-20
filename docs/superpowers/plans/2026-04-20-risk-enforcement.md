# Risk Enforcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close three production gaps that make risk limits unenforceable and P&L data unreliable: add broker-side GTC stop orders, write actual fill prices back to the trades table, and gate market_open/health_check sessions behind a scheduler-enforced -3% daily loss circuit breaker.

**Architecture:** All SQLite mutations stay in `scheduler/tools/sqlite.py`; scheduler-level logic stays in `scheduler/main.py`; Claude's tool schemas and OPERATIONS_MANUAL protocol text stay in `scheduler/agent.py`. No new files are created.

**Tech Stack:** Python 3.11, SQLite (WAL mode), Alpaca REST API, pytest, unittest.mock

---

### Task 1: Schema Migration — Add `alpaca_order_id` and `stop_order_id` Columns

**Files:**
- Modify: `scheduler/tools/sqlite.py` (lines 23–45 `_SCHEMA`, lines 110–117 `bootstrap_db`)
- Test: `tests/test_sqlite.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sqlite.py
import sqlite3
import pytest
from unittest.mock import patch


def test_schema_has_alpaca_order_id_and_stop_order_id(tmp_path):
    db = str(tmp_path / "trades.db")
    with patch("scheduler.tools.sqlite.DB_PATH", db):
        from scheduler.tools.sqlite import bootstrap_db
        bootstrap_db()
    conn = sqlite3.connect(db)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()]
    conn.close()
    assert "alpaca_order_id" in cols
    assert "stop_order_id" in cols
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_sqlite.py::test_schema_has_alpaca_order_id_and_stop_order_id -v
```

Expected: FAIL — `assert "alpaca_order_id" in cols`

- [ ] **Step 3: Add columns to `_SCHEMA` and `bootstrap_db`**

In `scheduler/tools/sqlite.py`, add the two columns to the `_SCHEMA` CREATE TABLE statement (after the `context_json` line, before `opened_at`):

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
    alpaca_order_id TEXT,
    stop_order_id   TEXT,
    opened_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    closed_at     TEXT
);
...
```

Then extend the idempotent ALTER TABLE block in `bootstrap_db`:

```python
def bootstrap_db() -> None:
    """Create the trades-db schema. Idempotent — safe to call on every startup."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        for stmt in [
            "ALTER TABLE trades ADD COLUMN strategy_version TEXT",
            "ALTER TABLE trades ADD COLUMN context_json TEXT",
            "ALTER TABLE trades ADD COLUMN alpaca_order_id TEXT",
            "ALTER TABLE trades ADD COLUMN stop_order_id TEXT",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_sqlite.py::test_schema_has_alpaca_order_id_and_stop_order_id -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_sqlite.py
git commit -m "feat: add alpaca_order_id and stop_order_id columns to trades schema"
```

---

### Task 2: `trade_update_fill` Tool

**Files:**
- Modify: `scheduler/tools/sqlite.py` (add function after `trade_open`)
- Test: `tests/test_sqlite.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sqlite.py (append to existing file)

def _open_trade(db: str, ticker="NVDA", entry_price=100.0, size=10.0) -> int:
    """Helper: insert a minimal open trade row, return trade_id."""
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.execute(
        "INSERT INTO trades (ticker, side, entry_price, size, setup_type, "
        "hypothesis_id, rationale, vix_at_entry, regime) "
        "VALUES (?, 'buy', ?, ?, 'momentum', 'H001', 'test', 15.0, 'bull_low_vol')",
        (ticker, entry_price, size),
    )
    conn.commit()
    trade_id = cursor.lastrowid
    conn.close()
    return trade_id


def test_trade_update_fill_writes_price_and_order_id(tmp_path):
    db = str(tmp_path / "trades.db")
    with patch("scheduler.tools.sqlite.DB_PATH", db):
        from scheduler.tools.sqlite import bootstrap_db, trade_update_fill
        bootstrap_db()
        trade_id = _open_trade(db)
        result = trade_update_fill(trade_id, 102.50, "alpaca-order-abc123")
    assert result == {"status": "ok", "trade_id": trade_id, "entry_price": 102.50}
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT entry_price, alpaca_order_id FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    assert row[0] == 102.50
    assert row[1] == "alpaca-order-abc123"


def test_trade_update_fill_rejects_unknown_trade(tmp_path):
    db = str(tmp_path / "trades.db")
    with patch("scheduler.tools.sqlite.DB_PATH", db):
        from scheduler.tools.sqlite import bootstrap_db, trade_update_fill
        bootstrap_db()
        with pytest.raises(ValueError, match="not found"):
            trade_update_fill(999, 100.0, "order-xyz")


def test_trade_update_fill_rejects_already_closed_trade(tmp_path):
    db = str(tmp_path / "trades.db")
    with patch("scheduler.tools.sqlite.DB_PATH", db):
        from scheduler.tools.sqlite import bootstrap_db, trade_update_fill
        bootstrap_db()
        trade_id = _open_trade(db)
        conn = sqlite3.connect(db)
        conn.execute(
            "UPDATE trades SET closed_at = datetime('now'), exit_price = 105.0, "
            "exit_reason = 'hit_target', outcome_pnl = 50.0, r_multiple = 1.0 WHERE id = ?",
            (trade_id,),
        )
        conn.commit()
        conn.close()
        with pytest.raises(ValueError, match="already closed"):
            trade_update_fill(trade_id, 102.0, "order-abc")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_sqlite.py::test_trade_update_fill_writes_price_and_order_id \
       tests/test_sqlite.py::test_trade_update_fill_rejects_unknown_trade \
       tests/test_sqlite.py::test_trade_update_fill_rejects_already_closed_trade -v
```

Expected: FAIL — `ImportError: cannot import name 'trade_update_fill'`

- [ ] **Step 3: Implement `trade_update_fill` in `sqlite.py`**

Add this function after `trade_open` (before `trade_close`) in `scheduler/tools/sqlite.py`. The entire body must be self-contained (Letta sandbox requirement — inline all imports):

```python
def trade_update_fill(
    trade_id: int,
    filled_avg_price: float,
    alpaca_order_id: str,
) -> dict:
    """Update entry_price and alpaca_order_id with actual fill data from Alpaca.

    Call this once after alpaca_list_orders confirms the entry order filled.
    Must be called before trade_close — entry_price is used for server-side P&L.

    Args:
        trade_id: ID returned by trade_open.
        filled_avg_price: Actual fill price from Alpaca (filled_avg_price field).
        alpaca_order_id: Alpaca order ID for the entry order.

    Returns:
        dict: {'status': 'ok', 'trade_id': int, 'entry_price': float}
    """
    import sqlite3
    from pathlib import Path

    db_path = DB_PATH
    if not Path(db_path).exists():
        raise RuntimeError("trades.db not found — run bootstrap first")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            "SELECT id, closed_at FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"trade_id {trade_id} not found")
        if row["closed_at"] is not None:
            raise ValueError(f"trade_id {trade_id} already closed — cannot update fill")
        conn.execute(
            "UPDATE trades SET entry_price = ?, alpaca_order_id = ? WHERE id = ?",
            (filled_avg_price, alpaca_order_id, trade_id),
        )
        conn.commit()
        return {"status": "ok", "trade_id": trade_id, "entry_price": filled_avg_price}
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_sqlite.py::test_trade_update_fill_writes_price_and_order_id \
       tests/test_sqlite.py::test_trade_update_fill_rejects_unknown_trade \
       tests/test_sqlite.py::test_trade_update_fill_rejects_already_closed_trade -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_sqlite.py
git commit -m "feat: add trade_update_fill tool for fill price writeback"
```

---

### Task 3: Server-Side P&L Computation in `trade_close`

**Files:**
- Modify: `scheduler/tools/sqlite.py` (`trade_close` function only)
- Test: `tests/test_sqlite.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_sqlite.py (append)

def test_trade_close_computes_pnl_server_side_long(tmp_path):
    """Long trade: entry 100, exit 110, size 10, stop_loss 95 → pnl=100, risk=50, r=2.0"""
    db = str(tmp_path / "trades.db")
    with patch("scheduler.tools.sqlite.DB_PATH", db):
        from scheduler.tools.sqlite import bootstrap_db, trade_close
        bootstrap_db()
        conn = sqlite3.connect(db)
        cursor = conn.execute(
            "INSERT INTO trades (ticker, side, entry_price, size, setup_type, "
            "hypothesis_id, rationale, vix_at_entry, regime, stop_loss) "
            "VALUES ('AAPL', 'buy', 100.0, 10.0, 'momentum', 'H001', 'test', 15.0, 'bull', 95.0)"
        )
        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()

        result = trade_close(trade_id, 110.0, "hit_target")

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT outcome_pnl, r_multiple FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(100.0)  # (110-100)*10
    assert row[1] == pytest.approx(2.0)    # 100 / ((100-95)*10)


def test_trade_close_computes_pnl_server_side_short(tmp_path):
    """Short trade: entry 100, exit 90, size 10, stop_loss 105 → pnl=100, risk=50, r=2.0"""
    db = str(tmp_path / "trades.db")
    with patch("scheduler.tools.sqlite.DB_PATH", db):
        from scheduler.tools.sqlite import bootstrap_db, trade_close
        bootstrap_db()
        conn = sqlite3.connect(db)
        cursor = conn.execute(
            "INSERT INTO trades (ticker, side, entry_price, size, setup_type, "
            "hypothesis_id, rationale, vix_at_entry, regime, stop_loss) "
            "VALUES ('SPY', 'sell', 100.0, 10.0, 'mean_reversion', 'H002', 'test', 15.0, 'bear', 105.0)"
        )
        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()

        result = trade_close(trade_id, 90.0, "hit_target")

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT outcome_pnl, r_multiple FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(100.0)  # (100-90)*10
    assert row[1] == pytest.approx(2.0)    # 100 / ((105-100)*10)


def test_trade_close_override_values_stored_when_provided(tmp_path):
    """If Claude provides outcome_pnl and r_multiple, those are used as-is."""
    db = str(tmp_path / "trades.db")
    with patch("scheduler.tools.sqlite.DB_PATH", db):
        from scheduler.tools.sqlite import bootstrap_db, trade_close
        bootstrap_db()
        conn = sqlite3.connect(db)
        cursor = conn.execute(
            "INSERT INTO trades (ticker, side, entry_price, size, setup_type, "
            "hypothesis_id, rationale, vix_at_entry, regime, stop_loss) "
            "VALUES ('MSFT', 'buy', 100.0, 10.0, 'momentum', 'H003', 'test', 15.0, 'bull', 95.0)"
        )
        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()

        trade_close(trade_id, 105.0, "manual", outcome_pnl=42.0, r_multiple=0.84)

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT outcome_pnl, r_multiple FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(42.0)
    assert row[1] == pytest.approx(0.84)


def test_trade_close_zero_risk_gives_r_zero(tmp_path):
    """No stop_loss set → risk = 0 → r_multiple = 0.0, not a division error."""
    db = str(tmp_path / "trades.db")
    with patch("scheduler.tools.sqlite.DB_PATH", db):
        from scheduler.tools.sqlite import bootstrap_db, trade_close
        bootstrap_db()
        conn = sqlite3.connect(db)
        cursor = conn.execute(
            "INSERT INTO trades (ticker, side, entry_price, size, setup_type, "
            "hypothesis_id, rationale, vix_at_entry, regime) "
            "VALUES ('TSLA', 'buy', 100.0, 5.0, 'momentum', 'H004', 'test', 15.0, 'bull')"
        )
        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()

        trade_close(trade_id, 110.0, "hit_target")

    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT outcome_pnl, r_multiple FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(50.0)
    assert row[1] == pytest.approx(0.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_sqlite.py::test_trade_close_computes_pnl_server_side_long \
       tests/test_sqlite.py::test_trade_close_computes_pnl_server_side_short \
       tests/test_sqlite.py::test_trade_close_override_values_stored_when_provided \
       tests/test_sqlite.py::test_trade_close_zero_risk_gives_r_zero -v
```

Expected: FAIL — current `trade_close` requires `outcome_pnl` and `r_multiple` as positional args

- [ ] **Step 3: Rewrite `trade_close` with server-side P&L**

Replace the entire `trade_close` function in `scheduler/tools/sqlite.py`:

```python
def trade_close(
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    outcome_pnl: Optional[float] = None,
    r_multiple: Optional[float] = None,
) -> dict:
    """Stamp exit fields onto an open trade. P&L is computed server-side from stored fields.

    outcome_pnl and r_multiple are optional overrides — if provided, they are stored
    as-is. If omitted, the tool computes them from entry_price, exit_price, stop_loss,
    size, and side. Use the override only when the fill price is unusual (e.g. gap open).

    Args:
        trade_id: ID returned by trade_open when the position was opened.
        exit_price: Fill price at exit.
        exit_reason: Why the trade was closed. One of: hit_target, stop_hit,
            thesis_invalidated, time_exit, manual, order_failed.
        outcome_pnl: Optional override for realised P&L in dollars.
        r_multiple: Optional override for outcome as a multiple of initial risk.

    Returns:
        dict: {'trade_id': int, 'closed_at': str, 'outcome_pnl': float, 'r_multiple': float}
    """
    import sqlite3
    from pathlib import Path

    db_path = DB_PATH
    if not Path(db_path).exists():
        raise RuntimeError("trades.db not found — run bootstrap first")

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row

    try:
        row = conn.execute(
            "SELECT id, closed_at, entry_price, stop_loss, size, side "
            "FROM trades WHERE id = ?",
            (trade_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"trade_id {trade_id} not found")
        if row["closed_at"] is not None:
            raise ValueError(f"trade_id {trade_id} already closed at {row['closed_at']}")

        # Compute P&L server-side if not provided as override
        if outcome_pnl is None:
            if row["side"] == "buy":
                outcome_pnl = (exit_price - row["entry_price"]) * row["size"]
            else:
                outcome_pnl = (row["entry_price"] - exit_price) * row["size"]

        if r_multiple is None:
            stop = row["stop_loss"]
            risk = abs(row["entry_price"] - stop) * row["size"] if stop is not None else 0.0
            r_multiple = outcome_pnl / risk if risk > 0 else 0.0

        conn.execute(
            """
            UPDATE trades
               SET exit_price  = ?,
                   exit_reason = ?,
                   outcome_pnl = ?,
                   r_multiple  = ?,
                   closed_at   = datetime('now')
             WHERE id = ?
            """,
            (exit_price, exit_reason, outcome_pnl, r_multiple, trade_id),
        )
        conn.commit()
        closed_at = conn.execute(
            "SELECT closed_at FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()["closed_at"]
        return {
            "trade_id": trade_id,
            "closed_at": closed_at,
            "outcome_pnl": outcome_pnl,
            "r_multiple": r_multiple,
        }
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_sqlite.py::test_trade_close_computes_pnl_server_side_long \
       tests/test_sqlite.py::test_trade_close_computes_pnl_server_side_short \
       tests/test_sqlite.py::test_trade_close_override_values_stored_when_provided \
       tests/test_sqlite.py::test_trade_close_zero_risk_gives_r_zero -v
```

Expected: all PASS

- [ ] **Step 5: Run the full test suite to catch regressions**

```bash
pytest tests/test_sqlite.py -v
```

Expected: all PASS (existing trade_close tests that passed positional args will need to be updated if they existed — if any break, update them to pass only trade_id/exit_price/exit_reason)

- [ ] **Step 6: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_sqlite.py
git commit -m "feat: trade_close computes P&L server-side, outcome_pnl/r_multiple now optional"
```

---

### Task 4: Register `trade_update_fill` and Update Agent Schemas + Protocol

**Files:**
- Modify: `scheduler/agent.py` (TOOL_SCHEMAS, `_build_tool_functions`, OPERATIONS_MANUAL text)
- Test: `tests/test_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_agent.py (append to existing file)

def test_trade_update_fill_in_tool_schemas():
    from scheduler.agent import TOOL_SCHEMAS
    names = [t["name"] for t in TOOL_SCHEMAS]
    assert "trade_update_fill" in names


def test_trade_update_fill_schema_has_required_fields():
    from scheduler.agent import TOOL_SCHEMAS
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "trade_update_fill")
    props = schema["input_schema"]["properties"]
    required = schema["input_schema"]["required"]
    assert "trade_id" in props
    assert "filled_avg_price" in props
    assert "alpaca_order_id" in props
    assert set(required) == {"trade_id", "filled_avg_price", "alpaca_order_id"}


def test_trade_close_schema_outcome_pnl_and_r_multiple_not_required():
    from scheduler.agent import TOOL_SCHEMAS
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "trade_close")
    required = schema["input_schema"]["required"]
    assert "outcome_pnl" not in required
    assert "r_multiple" not in required
    # But still present as properties (for optional override)
    props = schema["input_schema"]["properties"]
    assert "outcome_pnl" in props
    assert "r_multiple" in props


def test_trade_update_fill_in_build_tool_functions():
    from scheduler.agent import _build_tool_functions
    fns = _build_tool_functions()
    assert "trade_update_fill" in fns
    assert callable(fns["trade_update_fill"])
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_agent.py::test_trade_update_fill_in_tool_schemas \
       tests/test_agent.py::test_trade_update_fill_schema_has_required_fields \
       tests/test_agent.py::test_trade_close_schema_outcome_pnl_and_r_multiple_not_required \
       tests/test_agent.py::test_trade_update_fill_in_build_tool_functions -v
```

Expected: FAIL

- [ ] **Step 3: Add `trade_update_fill` to `TOOL_SCHEMAS`**

In `scheduler/agent.py`, add this entry to `TOOL_SCHEMAS` after the `trade_open` entry (before `trade_close`):

```python
    {
        "name": "trade_update_fill",
        "description": (
            "Update entry_price and alpaca_order_id with actual fill data from Alpaca. "
            "Call AFTER alpaca_list_orders confirms the entry order filled, BEFORE placing the stop order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trade_id": {"type": "integer"},
                "filled_avg_price": {"type": "number"},
                "alpaca_order_id": {"type": "string"},
            },
            "required": ["trade_id", "filled_avg_price", "alpaca_order_id"],
        },
    },
```

- [ ] **Step 4: Update `trade_close` schema — mark `outcome_pnl` and `r_multiple` optional**

In `scheduler/agent.py`, update the `trade_close` schema entry. Change `"required"` to remove the two computed fields:

```python
    {
        "name": "trade_close",
        "description": (
            "Stamp exit fields onto an open trade after the exit order fills. "
            "P&L is computed server-side from stored entry_price/stop_loss/size — "
            "do NOT pass outcome_pnl or r_multiple unless you have an override reason."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trade_id": {"type": "integer"},
                "exit_price": {"type": "number"},
                "exit_reason": {
                    "type": "string",
                    "enum": ["hit_target", "stop_hit", "thesis_invalidated", "time_exit", "manual", "order_failed"],
                },
                "outcome_pnl": {"type": "number"},
                "r_multiple": {"type": "number"},
            },
            "required": ["trade_id", "exit_price", "exit_reason"],
        },
    },
```

- [ ] **Step 5: Update `_build_tool_functions` to include `trade_update_fill`**

In `scheduler/agent.py`, update the import line and return dict in `_build_tool_functions`:

```python
def _build_tool_functions() -> dict:
    from scheduler.tools.sqlite import trade_open, trade_close, trade_update_fill, hypothesis_log, trade_query
    from scheduler.tools.alpaca import (
        alpaca_get_account, alpaca_get_positions, alpaca_place_order,
        alpaca_list_orders, alpaca_cancel_order,
    )
    from scheduler.tools.fmp import fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar
    from scheduler.tools.serper import serper_search
    from scheduler.tools.pyexec import run_script
    return {
        "trade_open": trade_open,
        "trade_update_fill": trade_update_fill,
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
```

- [ ] **Step 6: Update OPERATIONS_MANUAL — market_open protocol**

In `scheduler/agent.py`, find the market_open protocol section in `OPERATIONS_MANUAL`. Replace the current steps c–f with the updated 8-step protocol:

Find this block (approximately):
```
   c. trade_open(...)  → get trade_id
   d. alpaca_place_order(...)
   e. alpaca_list_orders(status="closed") — confirm fill. If not filled/rejected: trade_close(trade_id, 0, "order_failed", 0, 0)
   f. hypothesis_log(id, "testing", f"Opened trade_id {trade_id} at {fill_price}")
```

Replace with:
```
   c. trade_open(...)  → get trade_id
   d. alpaca_place_order(symbol, qty, side, order_type="market")
   e. alpaca_list_orders(status="closed") → confirm fill, get filled_avg_price + alpaca order_id
      If not filled or rejected: trade_close(trade_id, 0, "order_failed") immediately.
   f. trade_update_fill(trade_id, filled_avg_price, alpaca_order_id)
   g. alpaca_place_order(symbol, qty, opposite_side, order_type="stop",
                         stop_price=stop_loss, time_in_force="gtc")
      → store the returned stop order_id in today_context alongside trade_id
      (For longs, opposite_side="sell". For shorts, opposite_side="buy".)
   h. hypothesis_log(id, "testing", f"Opened trade_id {trade_id} at {filled_avg_price}")
```

- [ ] **Step 7: Update OPERATIONS_MANUAL — manual close protocol**

Find the existing close sequence reference in health_check and the general close instructions. Add a "Manual Close Protocol" section in the OPERATIONS_MANUAL after the market_open section:

```
### Manual Close Protocol (health_check and EOD)
Before closing any position manually:
1. Retrieve stop_order_id for this trade from today_context
2. alpaca_cancel_order(stop_order_id) — cancel the standing GTC stop
   If this returns an error, the stop already filled intraday. Treat this as:
   "stop was hit — record trade_close using stop_loss price as exit_price, exit_reason='stop_hit'"
   and skip placing a market order (position is already flat).
3. alpaca_place_order(symbol, qty, opposite_side, order_type="market") — exit the position
4. trade_close(trade_id, exit_price, exit_reason)
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
pytest tests/test_agent.py::test_trade_update_fill_in_tool_schemas \
       tests/test_agent.py::test_trade_update_fill_schema_has_required_fields \
       tests/test_agent.py::test_trade_close_schema_outcome_pnl_and_r_multiple_not_required \
       tests/test_agent.py::test_trade_update_fill_in_build_tool_functions -v
```

Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add scheduler/agent.py tests/test_agent.py
git commit -m "feat: register trade_update_fill, update market_open and close protocols, simplify trade_close schema"
```

---

### Task 5: Daily Halt Code Enforcement

**Files:**
- Modify: `scheduler/main.py` (add `_check_daily_halt`, wire into `job_market_open` and `job_health_check`)
- Test: `tests/test_main.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_main.py (append to existing file)

import sqlite3
import pytest
from unittest.mock import patch, MagicMock


def _make_db_with_pnl(tmp_path, pnl_values: list[float]) -> str:
    """Create a temp trades.db with today's closed trades summing to sum(pnl_values)."""
    db = str(tmp_path / "trades.db")
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT, side TEXT, entry_price REAL, size REAL,
            setup_type TEXT, hypothesis_id TEXT, rationale TEXT,
            vix_at_entry REAL, regime TEXT,
            outcome_pnl REAL, closed_at TEXT
        )
    """)
    for pnl in pnl_values:
        conn.execute(
            "INSERT INTO trades (ticker, side, entry_price, size, setup_type, "
            "hypothesis_id, rationale, vix_at_entry, regime, outcome_pnl, closed_at) "
            "VALUES ('AAPL','buy',100,10,'momentum','H1','t',15,'bull',?,datetime('now'))",
            (pnl,),
        )
    conn.commit()
    conn.close()
    return db


def _mock_alpaca_equity(equity: float) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"equity": str(equity)}
    return resp


def test_check_daily_halt_fires_at_minus_3_percent(tmp_path):
    db = _make_db_with_pnl(tmp_path, [-1500.0])  # -1500 on 50000 equity = -3%
    with patch("scheduler.main.DB_PATH", db), \
         patch("requests.get", return_value=_mock_alpaca_equity(50000.0)):
        from scheduler.main import _check_daily_halt
        assert _check_daily_halt() is True


def test_check_daily_halt_does_not_fire_below_threshold(tmp_path):
    db = _make_db_with_pnl(tmp_path, [-1000.0])  # -1000 on 50000 = -2% < -3%
    with patch("scheduler.main.DB_PATH", db), \
         patch("requests.get", return_value=_mock_alpaca_equity(50000.0)):
        from scheduler.main import _check_daily_halt
        assert _check_daily_halt() is False


def test_check_daily_halt_returns_false_with_no_trades(tmp_path):
    db = _make_db_with_pnl(tmp_path, [])  # no trades today
    with patch("scheduler.main.DB_PATH", db), \
         patch("requests.get", return_value=_mock_alpaca_equity(50000.0)):
        from scheduler.main import _check_daily_halt
        assert _check_daily_halt() is False


def test_check_daily_halt_returns_false_on_alpaca_error(tmp_path):
    db = _make_db_with_pnl(tmp_path, [-2000.0])
    error_resp = MagicMock()
    error_resp.status_code = 500
    with patch("scheduler.main.DB_PATH", db), \
         patch("requests.get", return_value=error_resp):
        from scheduler.main import _check_daily_halt
        assert _check_daily_halt() is False


def test_job_market_open_skips_when_halted(tmp_path):
    db = _make_db_with_pnl(tmp_path, [-1500.0])
    with patch("scheduler.main.DB_PATH", db), \
         patch("requests.get", return_value=_mock_alpaca_equity(50000.0)), \
         patch("scheduler.main.run_session") as mock_run, \
         patch("scheduler.main.send_telegram") as mock_telegram:
        from scheduler.main import job_market_open
        job_market_open()
    mock_run.assert_not_called()
    mock_telegram.assert_called_once()
    assert "HALT" in mock_telegram.call_args[0][0].upper()


def test_job_health_check_skips_when_halted(tmp_path):
    db = _make_db_with_pnl(tmp_path, [-1500.0])
    with patch("scheduler.main.DB_PATH", db), \
         patch("requests.get", return_value=_mock_alpaca_equity(50000.0)), \
         patch("scheduler.main.run_session") as mock_run, \
         patch("scheduler.main.send_telegram") as mock_telegram:
        from scheduler.main import job_health_check
        job_health_check()
    mock_run.assert_not_called()
    mock_telegram.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_main.py::test_check_daily_halt_fires_at_minus_3_percent \
       tests/test_main.py::test_check_daily_halt_does_not_fire_below_threshold \
       tests/test_main.py::test_check_daily_halt_returns_false_with_no_trades \
       tests/test_main.py::test_check_daily_halt_returns_false_on_alpaca_error \
       tests/test_main.py::test_job_market_open_skips_when_halted \
       tests/test_main.py::test_job_health_check_skips_when_halted -v
```

Expected: FAIL — `ImportError: cannot import name '_check_daily_halt'`

- [ ] **Step 3: Add `_check_daily_halt` to `main.py`**

Add this function in `scheduler/main.py` after `_build_recent_context_str` (before `run_session`):

```python
def _check_daily_halt() -> bool:
    """Return True if today's realized P&L has breached the -3% daily loss limit.

    Queries SQLite for today's closed trades, fetches current equity from Alpaca,
    and returns True only if sum_pnl / equity < -0.03. Fails open (returns False)
    on any error — a connectivity problem must not lock out all sessions.
    """
    import sqlite3
    import requests as _requests

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT SUM(outcome_pnl) as sum_pnl FROM trades "
            "WHERE date(closed_at) = date('now') AND closed_at IS NOT NULL"
        ).fetchone()
        conn.close()
        sum_pnl = float(row["sum_pnl"]) if row and row["sum_pnl"] is not None else 0.0
        if sum_pnl >= 0:
            return False  # profitable or flat — no halt

        resp = _requests.get(
            f"{os.environ['ALPACA_BASE_URL']}/v2/account",
            headers={
                "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return False
        equity = float(resp.json()["equity"])
        if equity <= 0:
            return False
        return (sum_pnl / equity) < -0.03
    except Exception as exc:
        log.warning(f"_check_daily_halt failed ({exc}) — failing open")
        return False
```

- [ ] **Step 4: Wire `_check_daily_halt` into `job_market_open` and `job_health_check`**

Replace `job_market_open` in `scheduler/main.py`:

```python
def job_market_open():
    if _check_daily_halt():
        log.warning("Daily halt active — skipping market_open session")
        send_telegram("DAILY HALT: -3% loss threshold reached. market_open skipped.")
        return
    now = datetime.now(ET)
    recent_context = _build_recent_context_str()
    prompt = build_market_open_prompt(
        date=now.strftime("%Y-%m-%d"),
        time_et="09:30",
        recent_context=recent_context,
    )
    run_session("market_open", prompt)
```

Replace `job_health_check` in `scheduler/main.py`:

```python
def job_health_check():
    if _check_daily_halt():
        log.warning("Daily halt active — skipping health_check session")
        send_telegram("DAILY HALT: -3% loss threshold reached. health_check skipped.")
        return
    now = datetime.now(ET)
    recent_context = _build_recent_context_str()
    prompt = build_health_check_prompt(
        date=now.strftime("%Y-%m-%d"),
        recent_context=recent_context,
    )
    run_session("health_check", prompt)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_main.py::test_check_daily_halt_fires_at_minus_3_percent \
       tests/test_main.py::test_check_daily_halt_does_not_fire_below_threshold \
       tests/test_main.py::test_check_daily_halt_returns_false_with_no_trades \
       tests/test_main.py::test_check_daily_halt_returns_false_on_alpaca_error \
       tests/test_main.py::test_job_market_open_skips_when_halted \
       tests/test_main.py::test_job_health_check_skips_when_halted -v
```

Expected: all PASS

- [ ] **Step 6: Run full test suite**

```bash
pytest -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add scheduler/main.py tests/test_main.py
git commit -m "feat: enforce daily -3% loss halt in job_market_open and job_health_check"
```

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
|---|---|
| `alpaca_order_id` and `stop_order_id` columns | Task 1 |
| `trade_update_fill` tool (updates entry_price + alpaca_order_id) | Task 2 |
| `trade_update_fill` validates trade is still open | Task 2 |
| `trade_close` computes P&L server-side from stored fields | Task 3 |
| Long and short P&L formulas correct | Task 3 |
| `outcome_pnl`/`r_multiple` accepted as optional overrides | Task 3 |
| `trade_update_fill` in TOOL_SCHEMAS and `_build_tool_functions` | Task 4 |
| `trade_close` schema marks `outcome_pnl`/`r_multiple` optional | Task 4 |
| market_open protocol updated (steps c-h) | Task 4 |
| Manual close protocol: cancel stop → place market → trade_close | Task 4 |
| `_check_daily_halt()` function | Task 5 |
| Halt wired into `job_market_open` | Task 5 |
| Halt wired into `job_health_check` | Task 5 |
| Halt fires at -3%, skips below, handles no-data, fails open on error | Task 5 |

**Placeholder scan:** No TBDs or incomplete sections.

**Type consistency:** `trade_update_fill(trade_id: int, filled_avg_price: float, alpaca_order_id: str)` — matches TOOL_SCHEMA properties and test calls throughout. `trade_close` optional params use `Optional[float]` which is already imported at module level in sqlite.py. `_check_daily_halt` uses module-level `DB_PATH` from main.py — matches test patches against `"scheduler.main.DB_PATH"`.

**Letta sandbox check:** `trade_update_fill` body inlines `import sqlite3` and `from pathlib import Path`. `trade_close` body already had this pattern — preserved. `_check_daily_halt` lives in `main.py` (not a Letta tool) so self-containment requirement does not apply.
