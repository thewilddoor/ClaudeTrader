"""
Main scheduler entrypoint.
Runs 5 recurring triggers + handles session dispatch, error recovery, and notifications.
"""
import os
import json
import time
import logging
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.agent import LettaTraderAgent
from scheduler.sessions import (
    build_pre_market_prompt,
    build_market_open_prompt,
    build_health_check_prompt,
    build_eod_reflection_prompt,
    build_weekly_review_prompt,
)
from scheduler.notifier import (
    parse_session_output,
    format_trade_notification,
    format_eod_summary,
    format_error_notification,
    format_alert,
    send_telegram,
)

ET = ZoneInfo("America/New_York")
AGENT_ID_FILE = Path("/app/.agent_id")
SESSION_TIMEOUT = 900  # 15 minutes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def get_agent() -> LettaTraderAgent:
    if not AGENT_ID_FILE.exists():
        raise RuntimeError("No agent ID found. Run bootstrap first: python -m scheduler.bootstrap")
    return LettaTraderAgent(agent_id=AGENT_ID_FILE.read_text().strip())


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


def run_session(session_type: str, prompt: str, max_retries: int = 1):
    """Dispatch a session to the Letta agent with error handling and Telegram notifications."""
    for attempt in range(max_retries + 1):
        try:
            log.info(f"Starting session: {session_type} (attempt {attempt + 1})")
            agent = get_agent()
            raw_output = agent.send_session(prompt)

            output = parse_session_output(raw_output)

            if session_type == "market_open" and output.get("trades"):
                for trade in output["trades"]:
                    send_telegram(format_trade_notification(trade))

            elif session_type == "health_check" and output.get("alerts"):
                for alert in output["alerts"]:
                    send_telegram(format_alert(alert))

            elif session_type == "eod_reflection" and output:
                send_telegram(format_eod_summary(output))

            elif session_type == "weekly_review" and output:
                send_telegram(f"📅 WEEKLY REVIEW COMPLETE\n{output.get('summary', '')}")

            log_path = Path(f"/app/logs/sessions/{date.today().isoformat()}_{session_type}.json")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps({"prompt": prompt, "output": raw_output, "parsed": output}, indent=2))

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
    prompt = build_pre_market_prompt(
        date=now.strftime("%Y-%m-%d"),
        market_opens_in="3h30m",
    )
    run_session("pre_market", prompt)


def job_market_open():
    now = datetime.now(ET)
    prompt = build_market_open_prompt(date=now.strftime("%Y-%m-%d"), time_et="09:30")
    run_session("market_open", prompt)


def job_health_check():
    now = datetime.now(ET)
    positions = _get_open_positions()
    prompt = build_health_check_prompt(date=now.strftime("%Y-%m-%d"), positions=positions)
    run_session("health_check", prompt)


def job_eod_reflection():
    now = datetime.now(ET)
    trades = _get_todays_trades()
    prompt = build_eod_reflection_prompt(date=now.strftime("%Y-%m-%d"), trades_today=trades)
    run_session("eod_reflection", prompt)


def job_weekly_review():
    now = datetime.now(ET)
    week_num = now.isocalendar()[1]
    prompt = build_weekly_review_prompt(date=now.strftime("%Y-%m-%d"), week_number=week_num)
    run_session("weekly_review", prompt)


def main():
    scheduler = BlockingScheduler(timezone=ET)

    scheduler.add_job(job_pre_market, CronTrigger(day_of_week="mon-fri", hour=6, minute=0, timezone=ET))
    scheduler.add_job(job_market_open, CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET))
    scheduler.add_job(job_health_check, CronTrigger(day_of_week="mon-fri", hour=13, minute=0, timezone=ET))
    scheduler.add_job(job_eod_reflection, CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=ET))
    scheduler.add_job(job_weekly_review, CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=ET))

    log.info("ClaudeTrading scheduler started. 5 jobs scheduled.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
