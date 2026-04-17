# tests/test_digester.py
from unittest.mock import MagicMock
import pytest


def test_summarize_calls_haiku_with_correct_model():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="DECISIONS MADE:\n- Opened NVDA long at $875")]
    mock_client.messages.create.return_value = mock_response

    from scheduler.digester import SessionDigester
    digester = SessionDigester(api_key="test-key", _client=mock_client)
    result = digester.summarize("pre_market session response text", "pre_market")

    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert call_kwargs["max_tokens"] == 400
    assert "DECISIONS MADE" in result


def test_summarize_truncates_long_responses():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="short digest")]
    mock_client.messages.create.return_value = mock_response

    from scheduler.digester import SessionDigester, DIGEST_MAX_CHARS
    digester = SessionDigester(api_key="test-key", _client=mock_client)
    long_response = "x" * (DIGEST_MAX_CHARS + 1000)
    digester.summarize(long_response, "eod_reflection")

    sent_content = mock_client.messages.create.call_args[1]["messages"][0]["content"]
    # Verify response was truncated (prompt with template should not exceed ~7500 chars)
    assert len(sent_content) < len(long_response) + 2000


def test_summarize_returns_empty_string_on_error():
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = Exception("API error")

    from scheduler.digester import SessionDigester
    digester = SessionDigester(api_key="test-key", _client=mock_client)
    result = digester.summarize("any response", "health_check")
    assert result == ""


def test_summarize_includes_session_name_in_prompt():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="digest text")]
    mock_client.messages.create.return_value = mock_response

    from scheduler.digester import SessionDigester
    digester = SessionDigester(api_key="test-key", _client=mock_client)
    digester.summarize("response", "weekly_review")

    sent_content = mock_client.messages.create.call_args[1]["messages"][0]["content"]
    assert "weekly_review" in sent_content
