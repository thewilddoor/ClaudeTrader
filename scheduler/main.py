"""
Main scheduler entrypoint.
Runs 5 recurring triggers + handles session dispatch, error recovery, and notifications.
"""
import os
import re
import json
import time
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.agent import AgentCore
from scheduler.memory import MemoryStore
from scheduler import strategy_gate
from scheduler.sessions import (
    build_pre_market_prompt,
    build_market_open_prompt,
    build_health_check_prompt,
    build_eod_reflection_prompt,
    build_weekly_review_prompt,
    build_recent_context,
)
from scheduler.tools.sqlite import backup_trades_db
from scheduler.notifier import (
    parse_session_output,
    format_error_notification,
    format_alert,
    format_probation_start,
    format_promotion,
    format_revert,
    format_gate_blocked,
    format_bypass_alert,
    send_telegram,
    send_telegram_long,
)

ET = ZoneInfo("America/New_York")
AGENT_ID_FILE = Path("/app/state/.agent_id")  # kept for backwards compat / tests
PENDING_FEEDBACK_PATH = Path("/app/state/pending_feedback.txt")
DB_PATH = os.environ.get("TRADES_DB_PATH", "/data/trades/trades.db")
SESSION_TIMEOUT = 900  # 15 minutes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def get_agent() -> AgentCore:
    return AgentCore()


def _read_and_clear_pending_feedback() -> Optional[str]:
    """Read and delete the pending feedback file. Returns None if it doesn't exist."""
    if not PENDING_FEEDBACK_PATH.exists():
        return None
    text = PENDING_FEEDBACK_PATH.read_text().strip()
    PENDING_FEEDBACK_PATH.unlink()
    return text or None


def _extract_strategy_version(doc_text: str) -> Optional[str]:
    """Extract the version field from a strategy doc metadata block."""
    match = re.search(r'^version:\s*(\S+)', doc_text, re.MULTILINE)
    return match.group(1) if match else None


def _get_open_positions() -> list:
    """Fetch current open positions from Alpaca for health check injection."""
    try:
        import requests
        resp = requests.get(
            f"{os.environ['ALPACA_BASE_URL']}/v2/positions",
            headers={
                "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
            },
            timeout=10,
        )
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def _get_todays_trades() -> list:
    """Fetch today's closed/filled orders from Alpaca for EOD injection."""
    try:
        import requests
        today = date.today().isoformat()
        resp = requests.get(
            f"{os.environ['ALPACA_BASE_URL']}/v2/orders",
            headers={
                "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
            },
            params={"status": "filled", "after": f"{today}T00:00:00Z", "limit": 50},
            timeout=10,
        )
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def _build_recent_context_str() -> str:
    """Fetch all data needed for recent_context and build the injected string."""
    from scheduler.tools.sqlite import _connect as _db_connect

    # Last 5 closed trades from SQLite
    last_trades = []
    try:
        conn = _db_connect(read_only=True)
        rows = conn.execute(
            "SELECT ticker, side, r_multiple, exit_reason, closed_at "
            "FROM trades WHERE closed_at IS NOT NULL "
            "ORDER BY closed_at DESC LIMIT 5"
        ).fetchall()
        last_trades = [dict(r) for r in rows]
        conn.close()
    except Exception:
        pass

    # Active hypotheses (formed or testing, not confirmed/rejected)
    active_hypotheses = []
    try:
        conn = _db_connect(read_only=True)
        rows = conn.execute(
            "SELECT hypothesis_id, body FROM hypothesis_log "
            "WHERE event_type IN ('formed', 'testing') "
            "ORDER BY logged_at DESC"
        ).fetchall()
        seen: set = set()
        for r in rows:
            if r["hypothesis_id"] not in seen:
                active_hypotheses.append(dict(r))
                seen.add(r["hypothesis_id"])
        conn.close()
    except Exception:
        pass

    # Live positions from Alpaca
    positions = _get_open_positions()

    # Current strategy version
    strategy_version = "v1"
    strategy_status = "confirmed"
    try:
        conn = _db_connect(read_only=True)
        row = conn.execute(
            "SELECT version, status FROM strategy_versions "
            "WHERE status IN ('confirmed', 'probationary') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row:
            strategy_version = row["version"]
            strategy_status = row["status"]
        conn.close()
    except Exception:
        pass

    # Last 2 session digests
    last_digests = []
    try:
        memory = MemoryStore(db_path=DB_PATH)
        last_digests = memory.get_recent_digests(n=2)
    except Exception:
        pass

    return build_recent_context(
        last_trades=last_trades,
        active_hypotheses=active_hypotheses,
        positions=positions,
        strategy_version=strategy_version,
        strategy_status=strategy_status,
        last_digests=last_digests,
    )


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
            raw_output = agent.run_session(session_type, prompt)
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
                            send_telegram(format_gate_blocked(
                                proposed.get("description", ""),
                                e.avg_r_blocked,
                                e.trades_evaluated or 0,
                            ))
                        # Guard rejections: feedback already written to pending_feedback.txt
                else:
                    # Fallback: detect direct write by version mismatch
                    post_doc = str(agent.get_memory_block("strategy_doc") or "")
                    pre_version = _extract_strategy_version(str(pre_doc or ""))
                    post_version = _extract_strategy_version(post_doc)
                    if post_version and post_version != pre_version:
                        try:
                            # Strip existing metadata block so apply_change doesn't produce double headers
                            post_doc_body = re.sub(
                                r'^## Version metadata\n(?:[^\n]+\n)*\n', '', post_doc, flags=re.MULTILINE
                            )
                            strategy_gate.apply_change(
                                agent,
                                {"description": "direct write detected", "new_strategy_doc": post_doc_body},
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

            # --- Notification logic ---
            _REPORT_SESSIONS = {"pre_market", "market_open", "eod_reflection", "weekly_review"}
            if session_type in _REPORT_SESSIONS and raw_output:
                header = f"[{session_type.replace('_', ' ').upper()}]\n\n"
                send_telegram_long(header + raw_output)
            elif session_type == "health_check" and output.get("alerts"):
                for alert in output["alerts"]:
                    send_telegram(format_alert(alert))

            try:
                log_path = Path(f"/app/logs/sessions/{date.today().isoformat()}_{session_type}.json")
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(json.dumps({"prompt": prompt, "output": raw_output, "parsed": output}, indent=2))
            except Exception as log_err:
                log.warning(f"Could not write session log: {log_err}")

            log.info(f"Session {session_type} complete.")
            return

        except Exception as e:
            log.error(f"Session {session_type} failed (attempt {attempt + 1}): {e}")
            if attempt < max_retries:
                log.info(f"Retrying in 60s...")
                time.sleep(60)
            else:
                send_telegram(format_error_notification(session_type, str(e)))


def job_pre_market():
    now = datetime.now(ET)
    recent_context = _build_recent_context_str()
    prompt = build_pre_market_prompt(
        date=now.strftime("%Y-%m-%d"),
        market_opens_in="3h30m",
        recent_context=recent_context,
    )
    run_session("pre_market", prompt)


def job_market_open():
    now = datetime.now(ET)
    recent_context = _build_recent_context_str()
    prompt = build_market_open_prompt(
        date=now.strftime("%Y-%m-%d"),
        time_et="09:30",
        recent_context=recent_context,
    )
    run_session("market_open", prompt)


def job_health_check():
    now = datetime.now(ET)
    recent_context = _build_recent_context_str()
    prompt = build_health_check_prompt(
        date=now.strftime("%Y-%m-%d"),
        recent_context=recent_context,
    )
    run_session("health_check", prompt)


def job_eod_reflection():
    now = datetime.now(ET)
    trades = _get_todays_trades()
    pending_feedback = _read_and_clear_pending_feedback()
    recent_context = _build_recent_context_str()
    prompt = build_eod_reflection_prompt(
        date=now.strftime("%Y-%m-%d"),
        trades_today=trades,
        recent_context=recent_context,
        pending_feedback=pending_feedback,
    )
    run_session("eod_reflection", prompt)


def job_weekly_review():
    now = datetime.now(ET)
    week_num = now.isocalendar()[1]
    pending_feedback = _read_and_clear_pending_feedback()
    recent_context = _build_recent_context_str()
    prompt = build_weekly_review_prompt(
        date=now.strftime("%Y-%m-%d"),
        week_number=week_num,
        recent_context=recent_context,
        pending_feedback=pending_feedback,
    )
    run_session("weekly_review", prompt)


def job_backup_db():
    try:
        backup_trades_db()
        log.info("trades.db backup complete.")
    except Exception as e:
        log.error(f"trades.db backup failed: {e}")
        send_telegram(format_error_notification("backup_db", str(e)))


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


if __name__ == "__main__":
    main()
