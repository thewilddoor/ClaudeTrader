# scheduler/tools/sqlite.py
"""
SQLite trade store. Four tools registered with Letta plus a backup utility.

DB_PATH is a module-level constant so tests can monkeypatch it.
All connections use WAL mode + 5 s busy_timeout.

IMPORTANT: Each Letta-registered function must be fully self-contained (imports,
helpers inlined) because Letta's upsert_from_function extracts only the function
body and runs it in an isolated sandbox with no access to module-level code.
"""
import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = "/data/trades/trades.db"
BACKUP_PATH = "/data/trades/trades.backup.db"

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
    strategy_version TEXT,
    context_json  TEXT,
    alpaca_order_id TEXT,
    stop_order_id   TEXT,
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


def _connect(read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
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


def bootstrap_memory_table(initial_values: dict) -> None:
    """Create memory and session_log tables. Insert initial_values without overwriting existing rows.

    Idempotent — safe to call on every startup.
    """
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
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
        cursor = conn.execute(
            "INSERT INTO hypothesis_log (hypothesis_id, event_type, body) VALUES (?, ?, ?)",
            (hypothesis_id, event_type, body),
        )
        conn.commit()
        return {"log_id": cursor.lastrowid}
    finally:
        conn.close()


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
    import sqlite3
    from pathlib import Path

    db_path = DB_PATH
    if not Path(db_path).exists():
        raise RuntimeError("trades.db not found — run bootstrap first")

    blocked = frozenset({"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "PRAGMA"})
    upper = sql.upper()
    for kw in blocked:
        if kw in upper:
            raise ValueError(
                f"trade_query is read-only — SQL contains blocked keyword '{kw}'. "
                "Use trade_open, trade_close, or hypothesis_log to write data."
            )

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row

    try:
        rows = conn.execute(sql).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


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
