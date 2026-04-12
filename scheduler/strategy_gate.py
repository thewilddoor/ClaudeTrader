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


def check_probation(agent) -> Optional[dict]:
    """
    Evaluate the active probationary strategy version if it has enough closed trades.

    Returns None if no probationary version or trade count < promote_after.

    Returns a dict on promotion or reversion:
      {outcome, version, trade_count, new_win_rate, new_avg_r,
       baseline_win_rate, baseline_avg_r, revert_reason}

    Letta write precedes DB commit in both paths — mirrors apply_change's ordering guarantee.
    """
    # Phase 1: read all DB data needed for the decision (read-only; closed before Letta I/O)
    conn = _connect(read_only=True)
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

        # Fetch confirmed doc now for the reversion path (before closing connection)
        confirmed_doc: Optional[str] = None
        if wr_degraded or ar_degraded:
            confirmed_row = conn.execute(
                "SELECT doc_text FROM strategy_versions "
                "WHERE status='confirmed' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            confirmed_doc = confirmed_row["doc_text"] if confirmed_row else None
    finally:
        conn.close()

    if not wr_degraded and not ar_degraded:
        # Phase 2: Letta write (promotion — before DB commit)
        current_doc = str(agent.get_memory_block("strategy_doc") or "")
        updated_doc = current_doc.replace("status: probationary", "status: confirmed")
        agent.update_memory_block("strategy_doc", updated_doc)

        # Phase 3: DB commit — also update doc_text so revert to this version restores confirmed text
        conn = _connect()
        try:
            conn.execute(
                "UPDATE strategy_versions SET status='confirmed', resolved_at=datetime('now'), "
                "doc_text=? WHERE version=?",
                (updated_doc, version),
            )
            conn.commit()
        finally:
            conn.close()

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

        # Phase 2: Letta write (reversion — before DB commit)
        if confirmed_doc is not None:
            agent.update_memory_block("strategy_doc", confirmed_doc)

        # Phase 3: DB commit
        conn = _connect()
        try:
            conn.execute(
                "UPDATE strategy_versions SET status='reverted', resolved_at=datetime('now'), "
                "revert_reason=? WHERE version=?",
                (revert_reason, version),
            )
            conn.commit()
        finally:
            conn.close()

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
