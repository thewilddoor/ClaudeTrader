# scheduler/sessions.py
import json
from typing import Optional


def build_recent_context(
    last_trades: list,
    active_hypotheses: list,
    positions: list,
    strategy_version: str,
    strategy_status: str,
    last_digests: list,
) -> str:
    """Build the ephemeral recent_context block injected into each session's user message."""
    trades_str = "\n".join(
        f"  {t.get('ticker', '?')} {t.get('side', '?')} "
        f"→ {float(t.get('r_multiple', 0)):+.2f}R "
        f"({t.get('exit_reason', '?')}) [{str(t.get('closed_at', ''))[:10]}]"
        for t in last_trades
    ) or "  None"

    hyp_str = "\n".join(
        f"  {h.get('hypothesis_id', '?')}: {str(h.get('body', ''))[:80]}"
        for h in active_hypotheses
    ) or "  None"

    pos_str = "\n".join(
        f"  {p.get('symbol', '?')} {p.get('qty', '?')}sh "
        f"@ ${float(p.get('avg_entry_price', 0)):.2f} | "
        f"unrealized: ${float(p.get('unrealized_pl', 0)):+.0f}"
        for p in positions
    ) or "  None"

    digest_section = ""
    if last_digests:
        digest_section = "\n**Previous Session Digests:**\n"
        for d in last_digests:
            digest_section += (
                f"[{d.get('session_name', '?')} {d.get('date', '?')}]\n"
                f"{d.get('digest', '')}\n\n"
            )

    return (
        f"## Recent Context (scheduler-injected)\n"
        f"**Strategy Version:** {strategy_version} ({strategy_status})\n"
        f"**Live Positions ({len(positions)} open):**\n{pos_str}\n"
        f"**Last 5 Closed Trades:**\n{trades_str}\n"
        f"**Active Hypotheses:**\n{hyp_str}"
        f"{digest_section}"
    )


def build_pre_market_prompt(date: str, market_opens_in: str, recent_context: str) -> str:
    return (
        f"SESSION: pre_market | DATE: {date} | MARKET_OPENS_IN: {market_opens_in}\n\n"
        f"{recent_context}\n\n"
        f"Begin pre_market session. Screen for today's opportunities, determine regime, "
        f"build watchlist. Respond with valid JSON only — begin with {{ and end with }}."
    )


def build_market_open_prompt(date: str, time_et: str, recent_context: str) -> str:
    return (
        f"SESSION: market_open | DATE: {date} | TIME: {time_et} ET\n\n"
        f"{recent_context}\n\n"
        f"Market just opened. Execute planned trades from today_context and watchlist "
        f"where conditions are met. Remember: trade_open BEFORE alpaca_place_order. "
        f"No proposed_change in this session. Respond with valid JSON only — begin with {{ and end with }}."
    )


def build_health_check_prompt(date: str, recent_context: str) -> str:
    return (
        f"SESSION: health_check | DATE: {date} | TIME: 13:00 ET\n\n"
        f"{recent_context}\n\n"
        f"Midday check. Review each open position against its original thesis. "
        f"Close positions where thesis is invalidated or stop has been hit. "
        f"No proposed_change in health_check — system rejects it. Respond with valid JSON only — begin with {{ and end with }}."
    )


def build_eod_reflection_prompt(
    date: str,
    trades_today: list,
    recent_context: str,
    pending_feedback: Optional[str] = None,
) -> str:
    trades_json = json.dumps(trades_today)
    feedback_section = (
        f"\n**Pending Feedback from system/operator:** {pending_feedback}"
        if pending_feedback else ""
    )
    return (
        f"SESSION: eod_reflection | DATE: {date} | TIME: 15:45 ET\n\n"
        f"{recent_context}{feedback_section}\n\n"
        f"**Today's Trades (from scheduler):** {trades_json}\n\n"
        f"End of day. Close remaining positions (unless overnight hold explicitly justified "
        f"in today_context). Refresh performance_snapshot from trade_query. Write observations. "
        f"Propose strategy changes via proposed_change if patterns across >=3 trades justify it. "
        f"Respond with valid JSON only — begin with {{ and end with }}. Include performance_update and proposed_change (or null)."
    )


def build_weekly_review_prompt(
    date: str,
    week_number: int,
    recent_context: str,
    pending_feedback: Optional[str] = None,
) -> str:
    feedback_section = (
        f"\n**Pending Feedback:** {pending_feedback}"
        if pending_feedback else ""
    )
    return (
        f"SESSION: weekly_review | DATE: {date} | WEEK: {week_number}\n\n"
        f"{recent_context}{feedback_section}\n\n"
        f"Weekly deep review. Mine trade data for patterns by setup_type, regime, VIX range, "
        f"and hypothesis. Confirm or reject hypotheses with sufficient data (>=10 trades). "
        f"Compress observations and watchlist. Update performance_snapshot. "
        f"Propose strategy changes if major patterns found. Respond with valid JSON only — begin with {{ and end with }}."
    )
