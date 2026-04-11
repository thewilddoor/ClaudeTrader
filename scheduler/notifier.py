import os
import re
import json
import requests
from typing import Optional


def parse_session_output(raw: str) -> dict:
    """
    Extract the last JSON object from Claude's session output.
    Claude emits a structured JSON block at the end of each session.
    """
    matches = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL)
    for match in reversed(matches):
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue
    return {}


def format_trade_notification(trade: dict) -> str:
    side = trade.get("side", "").upper()
    symbol = trade.get("symbol", "?")
    qty = trade.get("qty", 0)
    price = trade.get("filled_avg_price", 0)
    stop = trade.get("stop", "?")
    target = trade.get("target", "?")
    risk = trade.get("risk_pct", "?")
    return (
        f"📈 TRADE EXECUTED\n"
        f"{symbol} | {side} | {qty} shares @ ${price:.2f}\n"
        f"Stop: ${stop} | Target: ${target}\n"
        f"Risk: {risk}%"
    )


def format_eod_summary(summary: dict) -> str:
    date = summary.get("date", "?")
    trades = summary.get("trades", 0)
    pnl = summary.get("pnl", 0)
    win_rate = summary.get("win_rate_10", "?")
    avg_rr = summary.get("avg_rr", "?")
    version = summary.get("strategy_version", "?")
    changed = " (UPDATED)" if summary.get("strategy_changed") else " (unchanged)"
    lesson = summary.get("lesson", "")
    pnl_str = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
    return (
        f"📊 EOD SUMMARY — {date}\n"
        f"Trades: {trades} | P&L: {pnl_str}\n"
        f"Win rate (10): {win_rate}% | Avg R:R: {avg_rr}\n"
        f"Strategy: {version}{changed}\n"
        f"Lesson: {lesson}"
    )


def format_strategy_update(update: dict) -> str:
    version = update.get("new_version", "?")
    trigger = update.get("trigger", "?")
    change = update.get("change", "?")
    note = update.get("diagnostic_note", "")
    return (
        f"🔄 STRATEGY UPDATED → {version}\n"
        f"Trigger: {trigger}\n"
        f"Change: {change}\n"
        f"{note}"
    )


def format_error_notification(session: str, error: str) -> str:
    return (
        f"❌ SESSION ERROR\n"
        f"{session} failed: {error}\n"
        f"Action: retrying in 60s"
    )


def format_alert(message: str) -> str:
    return f"⚠️ ALERT\n{message}"


def send_telegram(
    message: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """Send a Telegram message. Returns True on success."""
    bot_token = bot_token or os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = chat_id or os.environ["TELEGRAM_CHAT_ID"]
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False
