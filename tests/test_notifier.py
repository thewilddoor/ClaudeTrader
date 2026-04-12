from unittest.mock import patch, MagicMock
import pytest
from scheduler.notifier import (
    parse_session_output,
    format_trade_notification,
    format_eod_summary,
    format_error_notification,
    send_telegram,
    format_probation_start,
    format_promotion,
    format_revert,
    format_gate_blocked,
    format_bypass_alert,
)


def test_parse_session_output_extracts_json():
    raw = 'Some reasoning text...\n{"status": "ok", "trades": [{"symbol": "NVDA", "side": "buy", "qty": 10}]}'
    result = parse_session_output(raw)
    assert result["status"] == "ok"
    assert result["trades"][0]["symbol"] == "NVDA"


def test_parse_session_output_handles_no_json():
    result = parse_session_output("No JSON here, just text")
    assert result == {}


def test_format_trade_notification():
    trade = {"symbol": "NVDA", "side": "buy", "qty": 47, "filled_avg_price": 891.20, "stop": 872.0, "target": 945.0, "risk_pct": 1.8}
    msg = format_trade_notification(trade)
    assert "NVDA" in msg
    assert "891.20" in msg
    assert "872.0" in msg


def test_format_eod_summary():
    summary = {"date": "2026-04-10", "trades": 3, "pnl": 1240.0, "win_rate_10": 60.0, "avg_rr": 1.8, "strategy_version": "v4", "strategy_changed": False, "lesson": "Momentum works when volume confirms."}
    msg = format_eod_summary(summary)
    assert "2026-04-10" in msg
    assert "1,240" in msg or "1240" in msg
    assert "60" in msg


def test_format_error_notification():
    msg = format_error_notification(session="eod_reflection", error="Letta timeout")
    assert "eod_reflection" in msg
    assert "Letta timeout" in msg


@patch("scheduler.notifier.requests.post")
def test_send_telegram_calls_api(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    send_telegram("test message", bot_token="token123", chat_id="456")
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "token123" in str(call_kwargs)
    assert "test message" in str(call_kwargs)


def test_format_probation_start():
    msg = format_probation_start("v6", 10, "Tighten RSI threshold to 65")
    assert "v6" in msg
    assert "10" in msg
    assert "Tighten RSI threshold to 65" in msg


def test_format_promotion():
    msg = format_promotion("v6", 10, 62.0, 1.6, 55.0, 1.4)
    assert "v6" in msg
    assert "62" in msg
    assert "1.6" in msg
    assert "55" in msg
    assert "1.4" in msg


def test_format_revert():
    msg = format_revert("v6", 55.0, 1.4, 38.0, 0.7)
    assert "v6" in msg
    assert "55" in msg
    assert "38" in msg
    assert "0.7" in msg


def test_format_gate_blocked():
    msg = format_gate_blocked("Tighten RSI", 0.82, 14)
    assert "0.82" in msg
    assert "14" in msg


def test_format_bypass_alert():
    msg = format_bypass_alert("v7")
    assert "v7" in msg
