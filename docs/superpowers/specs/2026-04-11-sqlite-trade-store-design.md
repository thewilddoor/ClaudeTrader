# SQLite Trade Store Design

**Date:** 2026-04-11  
**Status:** Approved  
**Scope:** Replace Letta recall memory as the trade record store with a SQLite database accessible to both the Letta agent and the scheduler container.

---

## Problem

Letta's recall memory is fuzzy text search, not SQL. The self-learning loop depends on precise aggregations — win rate by setup type, average R-multiple in high-VIX regimes, hypothesis performance over the last N trades. Recall search returns text chunks; Claude then has to mentally aggregate them and will get the math wrong or miss records. The fix is to stop asking Letta to be a database.

---

## What Changes

- Trade records move from recall memory to SQLite.
- Four tools replace the ad-hoc recall memory pattern: `trade_open`, `trade_close`, `hypothesis_log`, `trade_query`.
- Recall memory returns to its intended purpose: hypothesis prose, session narratives, free-text notes.
- `performance_snapshot` in core memory becomes a cache refreshed by `trade_query` at EOD, not manually maintained text tallies.
- Letta retains everything it is good at: strategy doc, hypothesis prose, cross-session state.

---

## Architecture

```
letta container                         scheduler container
────────────────────────────────        ────────────────────────────────
trade_open(...)    ──► write ──►        bootstrap.py  (creates schema)
trade_close(...)   ──► write ──►  /data/trades/trades.db
hypothesis_log(...)──► write ──►        main.py  ──► read-only (ro URI)
trade_query(sql)   ──► read  ──►
```

**Single writer:** Letta is the sole writer. The scheduler opens the DB read-only.  
**Concurrency:** WAL mode + `busy_timeout = 5000ms` on every connection. WAL allows unlimited concurrent readers and one writer without blocking. The scheduler's read-only connections never block Letta writes.  
**Volume:** A named Docker volume `trades-db` mounted at `/data/trades` in both containers.

---

## Schema

Created by `bootstrap.py` before any Letta API call. Both tables use `CREATE TABLE IF NOT EXISTS` so bootstrap is idempotent.

```sql
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
    event_type    TEXT    NOT NULL CHECK(event_type IN ('formed','testing','confirmed','rejected','refined')),
    body          TEXT,
    logged_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

Exit columns (`exit_price`, `outcome_pnl`, `r_multiple`, `exit_reason`, `closed_at`) are nullable at open and populated by `trade_close`. Risk management columns (`stop_loss`, `take_profit`) are nullable and recorded at open — they mirror what Claude passes to Alpaca MCP and enable stop-tightness analysis later. `opened_at` and `closed_at` are set by the tool layer — Claude never passes timestamps.

---

## Tools

All four tools live in `scheduler/tools/sqlite.py` and are registered with Letta in `scheduler/tools/registry.py` alongside the existing eleven tools.

Every tool function checks for DB existence at entry:

```python
if not Path(DB_PATH).exists():
    raise RuntimeError("trades.db not found — run bootstrap first")
```

This prevents silent schema-less DB creation if a tool fires before bootstrap.

### `trade_open`

Records a new position at entry time.

**Parameters:** `ticker: str`, `side: str` (buy/sell), `entry_price: float`, `size: float`, `setup_type: str`, `hypothesis_id: str`, `rationale: str`, `vix_at_entry: float`, `regime: str`, `stop_loss: float | None`, `take_profit: float | None`  
**Returns:** `trade_id: int` — Claude stores this in session context to pass to `trade_close`.  
**Sets automatically:** `opened_at = datetime('now')`

### `trade_close`

Stamps exit fields onto an open trade. Claude cannot modify entry fields.

**Parameters:** `trade_id: int`, `exit_price: float`, `exit_reason: str`, `outcome_pnl: float`, `r_multiple: float`  
**Sets automatically:** `closed_at = datetime('now')`  
**Validation:** Raises if `trade_id` not found or trade already closed.

### `hypothesis_log`

Appends a lifecycle event to the hypothesis ledger.

**Parameters:** `hypothesis_id: str`, `event_type: str` (formed/testing/confirmed/rejected/refined), `body: str`  
**Sets automatically:** `logged_at = datetime('now')`

### `trade_query`

Executes a read-only SQL query and returns results as a list of dicts.

**Parameters:** `sql: str`  
**Enforcement:** Any SQL containing `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `CREATE`, or `PRAGMA` (write variants) is rejected before execution with a clear error. This is a keyword scan on the raw SQL string — it has a known false positive: a prohibited keyword appearing inside a string literal (e.g., `WHERE rationale LIKE '%decided to DELETE%'`) will be rejected. Claude can work around this by rephrasing the pattern or using a different filter. This is accepted as an edge case; a full SQL parser is not warranted here.  
**Example queries Claude will write:**

```sql
-- Win rate on momentum setups in high-VIX environments, last 30 trades
SELECT
    COUNT(*) AS total,
    AVG(CASE WHEN outcome_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
    AVG(r_multiple) AS avg_r
FROM trades
WHERE setup_type = 'momentum'
  AND vix_at_entry > 30
  AND closed_at IS NOT NULL
ORDER BY closed_at DESC
LIMIT 30;

-- Hypothesis performance summary
SELECT
    hypothesis_id,
    COUNT(*) AS trades,
    AVG(r_multiple) AS avg_r,
    AVG(CASE WHEN outcome_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
FROM trades
WHERE hypothesis_id IS NOT NULL
  AND closed_at IS NOT NULL
GROUP BY hypothesis_id;
```

---

## Connection Pattern

All connections — tool functions and scheduler reads — follow the same pattern:

```python
import sqlite3
from pathlib import Path

DB_PATH = "/data/trades/trades.db"

def _connect(read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    # On a read-only connection, PRAGMA journal_mode=WAL is harmless if the DB is
    # already in WAL mode (returns current mode without changing it). It will
    # silently fail if the DB is not in WAL mode — but bootstrap.py sets WAL before
    # any connection from the Letta tools or scheduler is ever opened, so this
    # case cannot arise in normal operation.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn
```

Letta tool functions call `_connect(read_only=False)` and close the connection after each call. The scheduler calls `_connect(read_only=True)`. Each call opens and closes its own connection — no connection pooling, no shared state.

---

## Bootstrap Sequence

`bootstrap.py` runs in the scheduler container on every startup (idempotent):

1. Open `/data/trades/trades.db` with WAL + busy_timeout.
2. Run `CREATE TABLE IF NOT EXISTS` for both tables.
3. Close connection.
4. Proceed with Letta agent init (create or recover agent ID).

The Letta tools assume the schema exists. If the DB file is missing at tool call time, they raise `RuntimeError("trades.db not found — run bootstrap first")` rather than silently creating an empty DB.

---

## Backup

A fifth APScheduler cron job runs daily at 2:00 AM ET in the scheduler container.

```python
import sqlite3

def backup_trades_db():
    src = sqlite3.connect("/data/trades/trades.db")
    dst = sqlite3.connect("/data/trades/trades.backup.db")
    src.backup(dst)
    dst.close()
    src.close()
```

`sqlite3.backup()` is used — not `shutil.copy2`. WAL-mode databases have up to three files (`trades.db`, `trades.db-wal`, `trades.db-shm`). A file copy may miss uncheckpointed writes in the WAL. The backup API performs an atomic, consistent snapshot regardless of WAL state.

The backup stays on the same volume (same VPS) as a first line of defense. Off-VPS backup (S3, rclone, etc.) is the recommended next step and is out of scope for this change.

---

## Docker Compose Changes

```yaml
volumes:
  letta-db:
  trades-db:          # new

services:
  letta:
    volumes:
      - letta-db:/root/.letta
      - trades-db:/data/trades    # new

  scheduler:
    volumes:
      - trades-db:/data/trades    # new
      - scripts:/app/scripts
      - logs:/app/logs
      - agent-state:/app/state
```

---

## What Letta Recall Memory Holds After This Change

Recall memory is no longer the trade store. It holds:

- **Hypothesis prose** — the reasoning behind a hypothesis, written in natural language, not structured events. (Structured lifecycle events go to `hypothesis_log`.)
- **Session narratives** — what Claude noticed intraday, market color, unusual behavior.
- **Free-text notes** — anything Claude wants to preserve across sessions that doesn't belong in a table.

Recall is no longer queried for aggregations. It is queried for context and narrative.

---

## `performance_snapshot` Core Memory Block

Becomes a cache, not a manually maintained tally. Claude refreshes it at EOD reflection via `trade_query`:

```sql
SELECT
    setup_type,
    COUNT(*) AS trades,
    AVG(CASE WHEN outcome_pnl > 0 THEN 1.0 ELSE 0.0 END) AS win_rate,
    AVG(r_multiple) AS avg_r
FROM trades
WHERE closed_at >= datetime('now', '-30 days')
GROUP BY setup_type;
```

The result is written back to the `performance_snapshot` block in human-readable form. This replaces Claude mentally summing trade records from fuzzy recall search.

---

## What Is Not Changing

- Letta remains the agent runtime. Session persistence, strategy doc, hypothesis prose, cross-session state — all stay in Letta.
- The eleven existing tools are unchanged.
- The five session schedule (pre_market, market_open, health_check, eod_reflection, weekly_review) is unchanged.
- Core memory blocks `strategy_doc`, `watchlist`, `today_context` are unchanged.
- The APScheduler cron infrastructure is unchanged (one new job added for backup).
