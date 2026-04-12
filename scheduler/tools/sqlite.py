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
