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

from scheduler.tools.sqlite import _connect, _BLOCKED_KEYWORDS

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
