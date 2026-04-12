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
