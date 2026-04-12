import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.send_session.return_value = '{"status": "ok"}'
    agent.get_memory_block.return_value = "## Version metadata\nversion: v1\nstatus: confirmed\n\nbody"
    return agent


def test_run_session_calls_apply_change_on_proposed_change(mock_agent, tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler.main.AGENT_ID_FILE", tmp_path / ".agent_id")
    (tmp_path / ".agent_id").write_text("agent-1")
    monkeypatch.setattr("scheduler.main.get_agent", lambda: mock_agent)

    mock_agent.send_session.return_value = (
        '{"status": "ok", "proposed_change": {"description": "test change", '
        '"new_strategy_doc": "new doc"}}'
    )

    apply_result = {"version": "v2", "promote_after": 20, "description": "test change"}
    with patch("scheduler.main.strategy_gate") as mock_gate:
        mock_gate.apply_change.return_value = apply_result
        mock_gate.check_probation.return_value = None
        mock_gate.StrategyGateError = Exception
        with patch("scheduler.main.send_telegram") as mock_tg:
            from scheduler.main import run_session
            run_session("eod_reflection", "prompt")

    mock_gate.apply_change.assert_called_once()
    mock_tg.assert_called()


def test_run_session_calls_check_probation_after_eod(mock_agent, tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler.main.AGENT_ID_FILE", tmp_path / ".agent_id")
    (tmp_path / ".agent_id").write_text("agent-1")
    monkeypatch.setattr("scheduler.main.get_agent", lambda: mock_agent)

    with patch("scheduler.main.strategy_gate") as mock_gate:
        mock_gate.apply_change.return_value = None
        mock_gate.check_probation.return_value = None
        mock_gate.StrategyGateError = Exception
        from scheduler.main import run_session
        run_session("eod_reflection", "prompt")

    mock_gate.check_probation.assert_called_once()


def test_run_session_does_not_call_gate_on_pre_market(mock_agent, tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler.main.AGENT_ID_FILE", tmp_path / ".agent_id")
    (tmp_path / ".agent_id").write_text("agent-1")
    monkeypatch.setattr("scheduler.main.get_agent", lambda: mock_agent)

    with patch("scheduler.main.strategy_gate") as mock_gate:
        from scheduler.main import run_session
        run_session("pre_market", "prompt")

    mock_gate.apply_change.assert_not_called()
    mock_gate.check_probation.assert_not_called()


def test_read_and_clear_pending_feedback_returns_content_and_deletes(tmp_path, monkeypatch):
    feedback_file = tmp_path / "pending_feedback.txt"
    feedback_file.write_text("v5 reverted.\nChange blocked.\n")
    monkeypatch.setattr("scheduler.main.PENDING_FEEDBACK_PATH", feedback_file)

    from scheduler.main import _read_and_clear_pending_feedback
    result = _read_and_clear_pending_feedback()

    assert "v5 reverted" in result
    assert "Change blocked" in result
    assert not feedback_file.exists()


def test_read_and_clear_returns_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("scheduler.main.PENDING_FEEDBACK_PATH", tmp_path / "no_such_file.txt")
    from scheduler.main import _read_and_clear_pending_feedback
    assert _read_and_clear_pending_feedback() is None
