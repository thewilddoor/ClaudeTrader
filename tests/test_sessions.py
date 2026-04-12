from scheduler.sessions import (
    build_pre_market_prompt,
    build_market_open_prompt,
    build_health_check_prompt,
    build_eod_reflection_prompt,
    build_weekly_review_prompt,
)


def test_pre_market_prompt_contains_session_type():
    prompt = build_pre_market_prompt(date="2026-04-10", market_opens_in="3h30m")
    assert "SESSION: pre_market" in prompt
    assert "2026-04-10" in prompt


def test_health_check_prompt_injects_positions():
    positions = [{"symbol": "NVDA", "qty": 10, "current_price": 900.0}]
    prompt = build_health_check_prompt(date="2026-04-10", positions=positions)
    assert "SESSION: health_check" in prompt
    assert "NVDA" in prompt


def test_eod_prompt_injects_trades():
    trades = [{"symbol": "NVDA", "side": "buy", "qty": 10, "filled_avg_price": 891.0, "status": "filled"}]
    prompt = build_eod_reflection_prompt(date="2026-04-10", trades_today=trades)
    assert "SESSION: eod_reflection" in prompt
    assert "NVDA" in prompt


def test_market_open_prompt_contains_time():
    prompt = build_market_open_prompt(date="2026-04-10", time_et="09:30")
    assert "SESSION: market_open" in prompt
    assert "09:30" in prompt


def test_weekly_review_prompt_contains_week():
    prompt = build_weekly_review_prompt(date="2026-04-13", week_number=16)
    assert "SESSION: weekly_review" in prompt
    assert "16" in prompt


def test_eod_prompt_includes_pending_feedback():
    prompt = build_eod_reflection_prompt("2026-04-12", [], pending_feedback="v5 was reverted.")
    assert "FEEDBACK" in prompt
    assert "v5 was reverted." in prompt


def test_eod_prompt_omits_feedback_when_none():
    prompt = build_eod_reflection_prompt("2026-04-12", [])
    assert "FEEDBACK" not in prompt


def test_weekly_prompt_includes_pending_feedback():
    prompt = build_weekly_review_prompt("2026-04-12", 15, pending_feedback="Change blocked.")
    assert "FEEDBACK" in prompt
    assert "Change blocked." in prompt


def test_weekly_prompt_omits_feedback_when_none():
    prompt = build_weekly_review_prompt("2026-04-12", 15)
    assert "FEEDBACK" not in prompt
