import json
import pytest
from scheduler.sessions import (
    build_pre_market_prompt,
    build_market_open_prompt,
    build_health_check_prompt,
    build_eod_reflection_prompt,
    build_weekly_review_prompt,
    build_recent_context,
)


def test_pre_market_prompt_contains_date_and_recent_context():
    ctx = "## Recent Context\n**Strategy Version:** v1 (confirmed)"
    result = build_pre_market_prompt("2026-04-16", "3h30m", ctx)
    assert "2026-04-16" in result
    assert "3h30m" in result
    assert "Recent Context" in result
    assert "pre_market" in result


def test_market_open_prompt_contains_trade_open_reminder():
    ctx = "## Recent Context\n**Live Positions:** 0 open"
    result = build_market_open_prompt("2026-04-16", "09:30", ctx)
    assert "trade_open" in result
    assert "BEFORE" in result.upper() or "before" in result


def test_health_check_prompt_contains_no_proposed_change_reminder():
    ctx = "## Recent Context\n**Live Positions:** 2 open"
    result = build_health_check_prompt("2026-04-16", ctx)
    assert "proposed_change" in result
    assert "health_check" in result


def test_eod_reflection_includes_trades_and_feedback():
    ctx = "## Recent Context\n**Strategy Version:** v2 (probationary)"
    trades = [{"ticker": "NVDA", "side": "buy", "r_multiple": 1.5}]
    result = build_eod_reflection_prompt("2026-04-16", trades, ctx, pending_feedback="Check RSI filter")
    assert "NVDA" in result
    assert "Check RSI filter" in result
    assert "proposed_change" in result


def test_eod_reflection_no_feedback_when_none():
    ctx = "## Recent Context"
    result = build_eod_reflection_prompt("2026-04-16", [], ctx, pending_feedback=None)
    assert "Pending Feedback" not in result


def test_weekly_review_includes_week_number():
    ctx = "## Recent Context"
    result = build_weekly_review_prompt("2026-04-16", 16, ctx)
    assert "16" in result
    assert "weekly_review" in result


def test_build_recent_context_formats_positions():
    positions = [{"symbol": "NVDA", "qty": "10", "avg_entry_price": "875.0", "unrealized_pl": "150.0"}]
    result = build_recent_context(
        last_trades=[],
        active_hypotheses=[],
        positions=positions,
        strategy_version="v1",
        strategy_status="confirmed",
        last_digests=[],
    )
    assert "NVDA" in result
    assert "v1" in result
    assert "confirmed" in result


def test_build_recent_context_includes_digests():
    digests = [
        {"session_name": "pre_market", "date": "2026-04-15", "digest": "DECISIONS MADE:\n- Opened NVDA"},
    ]
    result = build_recent_context(
        last_trades=[],
        active_hypotheses=[],
        positions=[],
        strategy_version="v1",
        strategy_status="confirmed",
        last_digests=digests,
    )
    assert "DECISIONS MADE" in result
    assert "pre_market" in result


def test_build_recent_context_shows_none_when_no_positions():
    result = build_recent_context(
        last_trades=[], active_hypotheses=[], positions=[],
        strategy_version="v1", strategy_status="confirmed", last_digests=[],
    )
    assert "None" in result


def test_pre_market_prompt_enforces_json_only():
    ctx = "## Recent Context\n**Strategy Version:** v1 (confirmed)"
    result = build_pre_market_prompt("2026-04-18", "3h30m", ctx)
    assert "Output ONLY the JSON object" in result
    assert "no preamble" in result


def test_market_open_prompt_enforces_json_only():
    ctx = "## Recent Context\n**Live Positions:** 0 open"
    result = build_market_open_prompt("2026-04-18", "09:30", ctx)
    assert "Output ONLY the JSON object" in result
    assert "no preamble" in result


def test_health_check_prompt_enforces_json_only():
    ctx = "## Recent Context\n**Live Positions:** 1 open"
    result = build_health_check_prompt("2026-04-18", ctx)
    assert "Output ONLY the JSON object" in result
    assert "no preamble" in result


def test_eod_reflection_prompt_enforces_json_only():
    ctx = "## Recent Context"
    result = build_eod_reflection_prompt("2026-04-18", [], ctx)
    assert "Output ONLY the JSON object" in result
    assert "no preamble" in result


def test_weekly_review_prompt_enforces_json_only():
    ctx = "## Recent Context"
    result = build_weekly_review_prompt("2026-04-18", 16, ctx)
    assert "Output ONLY the JSON object" in result
    assert "no preamble" in result
