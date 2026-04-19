"""SQLite-backed persistent memory store for trading agent."""
import sqlite3
from typing import Optional


class MemoryStore:
    """Manages persistent key-value memory and session logs."""

    def __init__(self, db_path: str = "/data/trades/trades.db"):
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        """Create and configure a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        return conn

    def read(self, key: str) -> Optional[str]:
        """Read a value from memory by key.

        Args:
            key: The memory key to read.

        Returns:
            The stored value, or None if the key does not exist.
        """
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM memory WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def write(self, key: str, value: str) -> None:
        """Write a value to memory. Creates the key if it doesn't exist.

        Args:
            key: The memory key to write.
            value: The value to store.
        """
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO memory (key, value, updated_at) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    def read_all(self) -> dict:
        """Read all key-value pairs from memory.

        Returns:
            A dict of all stored key-value pairs.
        """
        conn = self._connect()
        try:
            rows = conn.execute("SELECT key, value FROM memory").fetchall()
            return {row["key"]: row["value"] for row in rows}
        finally:
            conn.close()

    def log_session(self, session_name: str, date: str, raw_response: str) -> int:
        """Log a session execution.

        Args:
            session_name: Name of the session (e.g. 'pre_market', 'eod_reflection').
            date: Date of the session.
            raw_response: The raw response text from the agent.

        Returns:
            The ID of the logged session.
        """
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO session_log (session_name, date, raw_response) VALUES (?, ?, ?)",
                (session_name, date, raw_response),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def update_session_digest(self, session_id: int, digest: str) -> None:
        """Update the digest field of a logged session.

        Args:
            session_id: The ID of the session to update.
            digest: The digest text to store.
        """
        conn = self._connect()
        try:
            conn.execute("UPDATE session_log SET digest = ? WHERE id = ?", (digest, session_id))
            conn.commit()
        finally:
            conn.close()

    def get_recent_digests(self, n: int = 2) -> list:
        """Retrieve the n most recent session digests that are not null.

        Args:
            n: Maximum number of digests to return.

        Returns:
            A list of dicts with keys: session_name, date, digest.
            Ordered chronologically (oldest first).
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_name, date, digest FROM session_log "
                "WHERE digest IS NOT NULL ORDER BY logged_at DESC, id DESC LIMIT ?",
                (n,),
            ).fetchall()
            return list(reversed([dict(row) for row in rows]))
        finally:
            conn.close()
