# SQLite Trade Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Letta recall memory as the trade record store with a SQLite database shared between the Letta and scheduler containers, so Claude writes SQL for analytics instead of relying on fuzzy text search.

**Architecture:** A new `scheduler/tools/sqlite.py` module provides four Letta tools (`trade_open`, `trade_close`, `hypothesis_log`, `trade_query`) and a backup function called by a daily cron job. The DB lives on a shared Docker volume (`trades-db`) mounted at `/data/trades` in both containers. `bootstrap.py` creates the schema before any Letta API call; tool functions guard against the DB being missing.

**Tech Stack:** Python 3.11, `sqlite3` (stdlib), APScheduler, Docker Compose named volumes.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `scheduler/tools/sqlite.py` | `DB_PATH`, `_connect`, `_db_guard`, `bootstrap_db`, `trade_open`, `trade_close`, `hypothesis_log`, `trade_query`, `backup_trades_db` |
| Create | `tests/test_tools/test_sqlite.py` | All tests for the above |
| Modify | `scheduler/tools/registry.py` | Import and register the four Letta tools |
| Modify | `scheduler/bootstrap.py` | Call `bootstrap_db()` before agent init |
| Modify | `scheduler/main.py` | Add `job_backup_db` cron at 2 AM ET |
| Modify | `docker-compose.yml` | Add `trades-db` volume, mount in `letta` and `scheduler` |

---

### Task 1: Add `trades-db` Docker volume

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: Add `trades-db` volume and mount it in both services**

Replace the `volumes` block and the `letta`/`scheduler` volume lists in `docker-compose.yml`:

```yaml
services:
  letta:
    image: letta/letta:latest
    ports:
      - "8283:8283"
    volumes:
      - letta-db:/root/.letta
      - trades-db:/data/trades
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "python3 -c \"import urllib.request; urllib.request.urlopen('http://localhost:8283/v1/health', timeout=5)\" 2>/dev/null || exit 1"]
      interval: 15s
      timeout: 10s
      retries: 12
      start_period: 90s

  alpaca-mcp:
    build:
      context: .
      dockerfile: docker/alpaca-mcp/Dockerfile
    environment:
      - ALPACA_API_KEY=${ALPACA_API_KEY}
      - ALPACA_SECRET_KEY=${ALPACA_SECRET_KEY}
      - ALPACA_BASE_URL=${ALPACA_BASE_URL}
    ports:
      - "8000:8000"
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "python3 -c \"import socket; socket.create_connection(('localhost', 8000), timeout=3)\" 2>/dev/null || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 15s

  scheduler:
    build: .
    volumes:
      - ./scripts:/app/scripts
      - ./logs:/app/logs
      - agent-state:/app/state
      - trades-db:/data/trades
    env_file:
      - .env
    depends_on:
      letta:
        condition: service_healthy
      alpaca-mcp:
        condition: service_healthy
    restart: unless-stopped

volumes:
  letta-db:
  agent-state:
  trades-db:
```

- [ ] **Step 2: Verify docker-compose config parses**

```bash
docker compose config --quiet
```

Expected: no output (exit 0). Any error means a YAML syntax issue.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: add trades-db shared volume to letta and scheduler"
```

---

### Task 2: Core DB module — `_connect` and `bootstrap_db`

**Files:**
- Create: `scheduler/tools/sqlite.py`
- Create: `tests/test_tools/test_sqlite.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tools/test_sqlite.py`:

```python
# tests/test_tools/test_sqlite.py
import sqlite3
import pytest
import scheduler.tools.sqlite as sqlite_module
from scheduler.tools.sqlite import bootstrap_db


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Temporary isolated DB with schema created."""
    db_file = tmp_path / "trades.db"
    monkeypatch.setattr(sqlite_module, "DB_PATH", str(db_file))
    bootstrap_db()
    return db_file


def test_bootstrap_creates_trades_table(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_bootstrap_creates_hypothesis_log_table(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='hypothesis_log'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_bootstrap_is_idempotent(db, monkeypatch):
    # Calling bootstrap_db a second time should not raise
    monkeypatch.setattr(sqlite_module, "DB_PATH", str(db))
    bootstrap_db()  # second call — must not raise


def test_bootstrap_sets_wal_mode(db):
    conn = sqlite3.connect(str(db))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_tools/test_sqlite.py -v
```

Expected: `ModuleNotFoundError: No module named 'scheduler.tools.sqlite'`

- [ ] **Step 3: Create `scheduler/tools/sqlite.py` with `_connect` and `bootstrap_db`**

```python
# scheduler/tools/sqlite.py
"""
SQLite trade store. Four tools registered with Letta plus a backup utility.

DB_PATH is a module-level constant so tests can monkeypatch it.
All connections use WAL mode + 5 s busy_timeout.
"""
import sqlite3
from pathlib import Path

DB_PATH = "/data/trades/trades.db"

_BLOCKED_KEYWORDS = frozenset(
    {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "PRAGMA"}
)

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
"""


def _connect(read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    # On a read-only connection, PRAGMA journal_mode=WAL is harmless if the DB is
    # already in WAL mode (returns the current mode without changing it). It will
    # silently fail if the DB is not in WAL mode — but bootstrap_db() sets WAL
    # before any tool or scheduler connection is ever opened.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _db_guard() -> None:
    if not Path(DB_PATH).exists():
        raise RuntimeError("trades.db not found — run bootstrap first")


def bootstrap_db() -> None:
    """Create the trades-db schema. Idempotent — safe to call on every startup."""
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_tools/test_sqlite.py -v
```

Expected: 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_tools/test_sqlite.py
git commit -m "feat: sqlite module — _connect, bootstrap_db, schema"
```

---

### Task 3: `trade_open` tool

**Files:**
- Modify: `scheduler/tools/sqlite.py`
- Modify: `tests/test_tools/test_sqlite.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_tools/test_sqlite.py`:

```python
from scheduler.tools.sqlite import trade_open


def test_trade_open_returns_trade_id(db):
    result = trade_open(
        ticker="NVDA",
        side="buy",
        entry_price=875.50,
        size=10.0,
        setup_type="momentum",
        hypothesis_id="H001",
        rationale="Breaking out of 3-week base on volume",
        vix_at_entry=18.5,
        regime="bull_low_vol",
        stop_loss=855.00,
        take_profit=940.00,
    )
    assert "trade_id" in result
    assert isinstance(result["trade_id"], int)
    assert result["trade_id"] >= 1


def test_trade_open_persists_row(db):
    trade_open(
        ticker="TSLA",
        side="buy",
        entry_price=200.00,
        size=5.0,
        setup_type="momentum",
        hypothesis_id="H002",
        rationale="Gap up on earnings beat",
        vix_at_entry=22.0,
        regime="bull_high_vol",
        stop_loss=190.00,
        take_profit=225.00,
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT * FROM trades WHERE ticker = 'TSLA'").fetchone()
    conn.close()
    assert row is not None
    assert row["entry_price"] == 200.00
    assert row["stop_loss"] == 190.00
    assert row["take_profit"] == 225.00
    assert row["closed_at"] is None


def test_trade_open_without_stops(db):
    result = trade_open(
        ticker="AAPL",
        side="sell",
        entry_price=180.00,
        size=3.0,
        setup_type="mean_reversion",
        hypothesis_id="H003",
        rationale="Extended above VWAP",
        vix_at_entry=15.0,
        regime="range_low_vol",
    )
    assert "trade_id" in result
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT * FROM trades WHERE ticker = 'AAPL'").fetchone()
    conn.close()
    assert row["stop_loss"] is None
    assert row["take_profit"] is None


def test_trade_open_raises_if_no_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_module, "DB_PATH", str(tmp_path / "missing.db"))
    with pytest.raises(RuntimeError, match="run bootstrap first"):
        trade_open(
            ticker="X",
            side="buy",
            entry_price=1.0,
            size=1.0,
            setup_type="test",
            hypothesis_id="H000",
            rationale="test",
            vix_at_entry=20.0,
            regime="test",
        )
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_tools/test_sqlite.py::test_trade_open_returns_trade_id -v
```

Expected: `ImportError` or `AttributeError` — `trade_open` not defined yet.

- [ ] **Step 3: Implement `trade_open`**

Append to `scheduler/tools/sqlite.py`:

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
    stop_loss: float | None = None,
    take_profit: float | None = None,
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

    Returns:
        dict: {'trade_id': int} — pass this to trade_close when exiting.
    """
    _db_guard()
    conn = _connect()
    try:
        cursor = conn.execute(
            """
            INSERT INTO trades
                (ticker, side, entry_price, size, setup_type, hypothesis_id,
                 rationale, vix_at_entry, regime, stop_loss, take_profit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ticker, side, entry_price, size, setup_type, hypothesis_id,
             rationale, vix_at_entry, regime, stop_loss, take_profit),
        )
        conn.commit()
        return {"trade_id": cursor.lastrowid}
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_tools/test_sqlite.py -v
```

Expected: all tests pass (4 from Task 2 + 4 new).

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_tools/test_sqlite.py
git commit -m "feat: trade_open tool — record trade at entry"
```

---

### Task 4: `trade_close` tool

**Files:**
- Modify: `scheduler/tools/sqlite.py`
- Modify: `tests/test_tools/test_sqlite.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools/test_sqlite.py`:

```python
from scheduler.tools.sqlite import trade_close


def test_trade_close_stamps_exit_fields(db):
    result = trade_open(
        ticker="NVDA", side="buy", entry_price=875.50, size=10.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="Breakout", vix_at_entry=18.5, regime="bull_low_vol",
    )
    trade_id = result["trade_id"]
    trade_close(
        trade_id=trade_id,
        exit_price=910.00,
        exit_reason="hit_target",
        outcome_pnl=345.00,
        r_multiple=2.1,
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    assert row["exit_price"] == 910.00
    assert row["outcome_pnl"] == 345.00
    assert row["r_multiple"] == 2.1
    assert row["exit_reason"] == "hit_target"
    assert row["closed_at"] is not None


def test_trade_close_raises_if_trade_not_found(db):
    with pytest.raises(ValueError, match="trade_id 9999 not found"):
        trade_close(
            trade_id=9999,
            exit_price=100.0,
            exit_reason="test",
            outcome_pnl=0.0,
            r_multiple=0.0,
        )


def test_trade_close_raises_if_already_closed(db):
    result = trade_open(
        ticker="TSLA", side="buy", entry_price=200.0, size=5.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="Gap up", vix_at_entry=22.0, regime="bull_high_vol",
    )
    trade_id = result["trade_id"]
    trade_close(trade_id=trade_id, exit_price=220.0, exit_reason="target", outcome_pnl=100.0, r_multiple=1.5)
    with pytest.raises(ValueError, match="already closed"):
        trade_close(trade_id=trade_id, exit_price=230.0, exit_reason="target", outcome_pnl=150.0, r_multiple=2.0)
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_tools/test_sqlite.py::test_trade_close_stamps_exit_fields -v
```

Expected: `ImportError` — `trade_close` not defined yet.

- [ ] **Step 3: Implement `trade_close`**

Append to `scheduler/tools/sqlite.py`:

```python
def trade_close(
    trade_id: int,
    exit_price: float,
    exit_reason: str,
    outcome_pnl: float,
    r_multiple: float,
) -> dict:
    """Stamp exit fields onto an open trade.

    Claude cannot modify entry fields — only the five exit columns are written.
    closed_at is set automatically by the tool.

    Args:
        trade_id: ID returned by trade_open when the position was opened.
        exit_price: Fill price at exit.
        exit_reason: Why the trade was closed (e.g. 'hit_target', 'stop_hit', 'manual').
        outcome_pnl: Realised P&L in dollars (negative for a loss).
        r_multiple: Outcome expressed as a multiple of initial risk (1R = risked amount).

    Returns:
        dict: {'trade_id': int, 'closed_at': str}
    """
    _db_guard()
    conn = _connect()
    try:
        row = conn.execute("SELECT id, closed_at FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            raise ValueError(f"trade_id {trade_id} not found")
        if row["closed_at"] is not None:
            raise ValueError(f"trade_id {trade_id} already closed at {row['closed_at']}")
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
        return {"trade_id": trade_id, "closed_at": closed_at}
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_tools/test_sqlite.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_tools/test_sqlite.py
git commit -m "feat: trade_close tool — stamp exit fields with lifecycle validation"
```

---

### Task 5: `hypothesis_log` tool

**Files:**
- Modify: `scheduler/tools/sqlite.py`
- Modify: `tests/test_tools/test_sqlite.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools/test_sqlite.py`:

```python
from scheduler.tools.sqlite import hypothesis_log


def test_hypothesis_log_inserts_row(db):
    result = hypothesis_log(
        hypothesis_id="H001",
        event_type="formed",
        body="Momentum setups outperform in high-VIX bull regimes",
    )
    assert "log_id" in result
    assert isinstance(result["log_id"], int)


def test_hypothesis_log_records_all_event_types(db):
    for event_type in ("formed", "testing", "confirmed", "rejected", "refined"):
        hypothesis_log(hypothesis_id="H002", event_type=event_type, body=f"Event: {event_type}")
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT event_type FROM hypothesis_log WHERE hypothesis_id = 'H002'"
    ).fetchall()
    conn.close()
    assert len(rows) == 5


def test_hypothesis_log_sets_logged_at(db):
    hypothesis_log(hypothesis_id="H003", event_type="formed", body="test")
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT logged_at FROM hypothesis_log WHERE hypothesis_id = 'H003'"
    ).fetchone()
    conn.close()
    assert row["logged_at"] is not None
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_tools/test_sqlite.py::test_hypothesis_log_inserts_row -v
```

Expected: `ImportError` — `hypothesis_log` not defined yet.

- [ ] **Step 3: Implement `hypothesis_log`**

Append to `scheduler/tools/sqlite.py`:

```python
def hypothesis_log(
    hypothesis_id: str,
    event_type: str,
    body: str,
) -> dict:
    """Append a lifecycle event to the hypothesis ledger.

    Args:
        hypothesis_id: Hypothesis identifier (e.g. 'H001').
        event_type: Lifecycle stage — one of: formed, testing, confirmed, rejected, refined.
        body: Free-text description of this lifecycle event.

    Returns:
        dict: {'log_id': int}
    """
    _db_guard()
    conn = _connect()
    try:
        cursor = conn.execute(
            "INSERT INTO hypothesis_log (hypothesis_id, event_type, body) VALUES (?, ?, ?)",
            (hypothesis_id, event_type, body),
        )
        conn.commit()
        return {"log_id": cursor.lastrowid}
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_tools/test_sqlite.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_tools/test_sqlite.py
git commit -m "feat: hypothesis_log tool — append lifecycle events to hypothesis ledger"
```

---

### Task 6: `trade_query` tool

**Files:**
- Modify: `scheduler/tools/sqlite.py`
- Modify: `tests/test_tools/test_sqlite.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools/test_sqlite.py`:

```python
from scheduler.tools.sqlite import trade_query


def test_trade_query_returns_list_of_dicts(db):
    trade_open(
        ticker="NVDA", side="buy", entry_price=875.50, size=10.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="Breakout", vix_at_entry=18.5, regime="bull_low_vol",
    )
    result = trade_query("SELECT ticker, entry_price FROM trades")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["ticker"] == "NVDA"
    assert result[0]["entry_price"] == 875.50


def test_trade_query_aggregation(db):
    for price, pnl, r in [(875.50, 345.0, 2.1), (200.0, -50.0, -0.5), (180.0, 200.0, 1.8)]:
        res = trade_open(
            ticker="X", side="buy", entry_price=price, size=1.0,
            setup_type="momentum", hypothesis_id="H001",
            rationale="test", vix_at_entry=20.0, regime="bull_low_vol",
        )
        trade_close(
            trade_id=res["trade_id"], exit_price=price + pnl,
            exit_reason="test", outcome_pnl=pnl, r_multiple=r,
        )
    result = trade_query(
        "SELECT AVG(r_multiple) as avg_r FROM trades WHERE setup_type = 'momentum' AND closed_at IS NOT NULL"
    )
    assert len(result) == 1
    assert abs(result[0]["avg_r"] - ((2.1 + -0.5 + 1.8) / 3)) < 0.001


def test_trade_query_blocks_insert(db):
    with pytest.raises(ValueError, match="read-only"):
        trade_query("INSERT INTO trades (ticker) VALUES ('X')")


def test_trade_query_blocks_drop(db):
    with pytest.raises(ValueError, match="read-only"):
        trade_query("DROP TABLE trades")


def test_trade_query_blocks_update(db):
    with pytest.raises(ValueError, match="read-only"):
        trade_query("UPDATE trades SET ticker = 'Y' WHERE id = 1")


def test_trade_query_empty_result(db):
    result = trade_query("SELECT * FROM trades WHERE ticker = 'NONEXISTENT'")
    assert result == []
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_tools/test_sqlite.py::test_trade_query_returns_list_of_dicts -v
```

Expected: `ImportError` — `trade_query` not defined yet.

- [ ] **Step 3: Implement `trade_query`**

Append to `scheduler/tools/sqlite.py`:

```python
def trade_query(sql: str) -> list[dict]:
    """Execute a read-only SQL query against the trade store.

    Any SQL containing INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, or PRAGMA
    is rejected before execution. Known limitation: this is a keyword scan on
    the raw SQL string — a blocked keyword appearing inside a string literal
    (e.g. WHERE rationale LIKE '%decided to DELETE%') will be rejected.
    Rephrase the LIKE pattern if this occurs.

    Args:
        sql: A SELECT query string.

    Returns:
        list[dict]: Query results, one dict per row.
    """
    _db_guard()
    upper = sql.upper()
    for kw in _BLOCKED_KEYWORDS:
        if kw in upper:
            raise ValueError(
                f"trade_query is read-only — SQL contains blocked keyword '{kw}'. "
                "Use trade_open, trade_close, or hypothesis_log to write data."
            )
    conn = _connect(read_only=True)
    try:
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_tools/test_sqlite.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_tools/test_sqlite.py
git commit -m "feat: trade_query tool — SELECT-only analytics with blocked keyword guard"
```

---

### Task 7: `backup_trades_db`

**Files:**
- Modify: `scheduler/tools/sqlite.py`
- Modify: `tests/test_tools/test_sqlite.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools/test_sqlite.py`:

```python
from scheduler.tools.sqlite import backup_trades_db


def test_backup_creates_backup_file(db, monkeypatch, tmp_path):
    backup_file = tmp_path / "trades.backup.db"
    monkeypatch.setattr(sqlite_module, "BACKUP_PATH", str(backup_file))
    backup_trades_db()
    assert backup_file.exists()


def test_backup_contains_same_data(db, monkeypatch, tmp_path):
    backup_file = tmp_path / "trades.backup.db"
    monkeypatch.setattr(sqlite_module, "BACKUP_PATH", str(backup_file))
    trade_open(
        ticker="NVDA", side="buy", entry_price=875.50, size=10.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="Breakout", vix_at_entry=18.5, regime="bull_low_vol",
    )
    backup_trades_db()
    conn = sqlite3.connect(str(backup_file))
    rows = conn.execute("SELECT ticker FROM trades").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "NVDA"
```

- [ ] **Step 2: Run tests — expect failure**

```bash
pytest tests/test_tools/test_sqlite.py::test_backup_creates_backup_file -v
```

Expected: `ImportError` — `backup_trades_db` not defined, `BACKUP_PATH` not defined.

- [ ] **Step 3: Add `BACKUP_PATH` constant and `backup_trades_db` to `scheduler/tools/sqlite.py`**

Add after `DB_PATH`:

```python
BACKUP_PATH = "/data/trades/trades.backup.db"
```

Append the function:

```python
def backup_trades_db() -> None:
    """Create a consistent backup of trades.db using SQLite's backup API.

    shutil.copy2 is intentionally NOT used here: WAL-mode databases have up to
    three files (trades.db, trades.db-wal, trades.db-shm). A file copy may miss
    uncheckpointed writes in the WAL. sqlite3.backup() performs an atomic,
    consistent snapshot regardless of WAL state.

    Source: DB_PATH. Destination: BACKUP_PATH. Both are monkeypatchable for tests.
    """
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(BACKUP_PATH)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/test_tools/test_sqlite.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/sqlite.py tests/test_tools/test_sqlite.py
git commit -m "feat: backup_trades_db — atomic WAL-safe backup via sqlite3.backup API"
```

---

### Task 8: Register the four Letta tools

**Files:**
- Modify: `scheduler/tools/registry.py`

No new test needed — the registry integration is covered by existing `test_agent.py` patterns and the tools themselves are already tested.

- [ ] **Step 1: Add imports and register the four tools**

In `scheduler/tools/registry.py`, add the import after the alpaca imports:

```python
from scheduler.tools.sqlite import (
    trade_open,
    trade_close,
    hypothesis_log,
    trade_query,
)
```

Add the four tools to `ALL_TOOLS`:

```python
ALL_TOOLS = [
    fmp_screener,
    fmp_ohlcv,
    fmp_news,
    fmp_earnings_calendar,
    serper_search,
    run_script,
    alpaca_get_account,
    alpaca_get_positions,
    alpaca_place_order,
    alpaca_list_orders,
    alpaca_cancel_order,
    trade_open,
    trade_close,
    hypothesis_log,
    trade_query,
]
```

- [ ] **Step 2: Verify imports resolve**

```bash
python -c "from scheduler.tools.registry import ALL_TOOLS; print(len(ALL_TOOLS))"
```

Expected: `15`

- [ ] **Step 3: Commit**

```bash
git add scheduler/tools/registry.py
git commit -m "feat: register trade_open, trade_close, hypothesis_log, trade_query with Letta"
```

---

### Task 9: Wire `bootstrap.py` to create the schema first

**Files:**
- Modify: `scheduler/bootstrap.py`

- [ ] **Step 1: Add `bootstrap_db` call before agent init**

In `scheduler/bootstrap.py`, add the import at the top:

```python
from scheduler.tools.sqlite import bootstrap_db as init_trades_db
```

Then in the `bootstrap()` function, add the DB init as the very first step inside the `if AGENT_ID_FILE.exists()` guard (i.e., run even on re-runs, since it's idempotent):

```python
def bootstrap():
    print("Initialising SQLite trade store...")
    init_trades_db()
    print("Trade store ready.")

    if AGENT_ID_FILE.exists():
        print("Bootstrap already completed. Agent ID:", AGENT_ID_FILE.read_text().strip())
        return

    agent_name = os.environ.get("LETTA_AGENT_NAME", "claude_trader")
    print(f"Creating Letta agent '{agent_name}'...")
    agent = LettaTraderAgent.create_new(agent_name)
    print(f"Agent created: {agent.agent_id}")

    print("Registering tools...")
    tools = register_all_tools(agent.agent_id)
    print(f"Registered: {tools}")

    print("Attaching Alpaca MCP server...")
    ok = attach_alpaca_mcp(agent.agent_id)
    print(f"Alpaca MCP attached: {ok}")

    # Load script library index into agent memory
    index_path = Path("/app/scripts/indicators/index.json")
    if index_path.exists():
        index_content = index_path.read_text()
        agent.send_session(
            f"BOOTSTRAP: Load this indicator library index into your memory for future reference.\n\n{index_content}"
        )
        print("Indicator library loaded.")

    # Save agent ID for scheduler use
    AGENT_ID_FILE.write_text(agent.agent_id)
    print(f"Bootstrap complete. Agent ID saved to {AGENT_ID_FILE}")
```

Note: `init_trades_db()` is called **before** the early-return guard. This ensures the schema is always present on every startup, even after the initial agent bootstrap has already run.

- [ ] **Step 2: Verify bootstrap imports cleanly**

```bash
python -c "from scheduler.bootstrap import bootstrap; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add scheduler/bootstrap.py
git commit -m "feat: bootstrap.py — init SQLite schema before Letta agent setup"
```

---

### Task 10: Add daily backup cron job

**Files:**
- Modify: `scheduler/main.py`

- [ ] **Step 1: Add the backup job function and register the cron**

In `scheduler/main.py`, add the import at the top with the other scheduler imports:

```python
from scheduler.tools.sqlite import backup_trades_db
```

Add the job function after `job_weekly_review`:

```python
def job_backup_db():
    try:
        backup_trades_db()
        log.info("trades.db backup complete.")
    except Exception as e:
        log.error(f"trades.db backup failed: {e}")
        send_telegram(format_error_notification("backup_db", str(e)))
```

Register the cron in `main()`, after the existing five jobs:

```python
scheduler.add_job(job_backup_db, CronTrigger(hour=2, minute=0, timezone=ET))
```

The full `main()` function after the change:

```python
def main():
    scheduler = BlockingScheduler(timezone=ET)

    scheduler.add_job(job_pre_market, CronTrigger(day_of_week="mon-fri", hour=6, minute=0, timezone=ET))
    scheduler.add_job(job_market_open, CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET))
    scheduler.add_job(job_health_check, CronTrigger(day_of_week="mon-fri", hour=13, minute=0, timezone=ET))
    scheduler.add_job(job_eod_reflection, CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=ET))
    scheduler.add_job(job_weekly_review, CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=ET))
    scheduler.add_job(job_backup_db, CronTrigger(hour=2, minute=0, timezone=ET))

    log.info("ClaudeTrading scheduler started. 6 jobs scheduled.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")
```

- [ ] **Step 2: Verify main.py imports cleanly**

```bash
python -c "from scheduler.main import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run the full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass. No regressions.

- [ ] **Step 4: Commit**

```bash
git add scheduler/main.py
git commit -m "feat: daily 2 AM backup cron for trades.db"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| `trades-db` shared Docker volume, both containers | Task 1 |
| WAL + busy_timeout on every connection | Task 2 (`_connect`) |
| Read-only URI for non-Letta connections | Task 6 (`trade_query`), Task 7 (`backup_trades_db` uses write connection — correct, backup writes a new file) |
| Schema created by bootstrap before Letta init | Tasks 2 + 9 |
| `_db_guard` raises `RuntimeError` if DB missing | Tasks 2, 3, 4, 5, 6 |
| `trade_open` with all fields including `stop_loss`, `take_profit` | Task 3 |
| `trade_close` auto-sets `closed_at`, validates not-found + already-closed | Task 4 |
| `hypothesis_log` with five event types | Task 5 |
| `trade_query` SELECT-only, blocked keywords, false positive documented | Task 6 |
| `BACKUP_PATH` constant, `backup_trades_db` uses `sqlite3.backup()` not `shutil.copy2` | Task 7 |
| Four tools registered in registry | Task 8 |
| `bootstrap.py` calls `bootstrap_db` before agent init, idempotent | Task 9 |
| Backup cron at 2 AM ET, error → Telegram | Task 10 |
| WAL pragma comment on read-only connections | Task 2 (`_connect` docstring/comment) |

**Placeholder scan:** None found.

**Type consistency check:**
- `trade_open` returns `{"trade_id": int}` — `trade_close` accepts `trade_id: int` ✓
- `hypothesis_log` returns `{"log_id": int}` — not referenced elsewhere ✓
- `trade_query` returns `list[dict]` ✓
- `DB_PATH` and `BACKUP_PATH` are both `str` constants at module level ✓
- `_db_guard` is called in `trade_open`, `trade_close`, `hypothesis_log`, `trade_query` — all four Letta tools ✓
- `backup_trades_db` does NOT call `_db_guard` — it calls `sqlite3.connect(DB_PATH)` directly, which will raise naturally if the file doesn't exist. This is correct: the backup cron should surface a real error, not a bootstrap error message ✓
