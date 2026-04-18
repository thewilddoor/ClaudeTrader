# tests/test_agent.py
import json
import time
from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture
def mem_db(tmp_path):
    import scheduler.tools.sqlite as sqlite_mod
    db_path = str(tmp_path / "test.db")
    original = sqlite_mod.DB_PATH
    sqlite_mod.DB_PATH = db_path
    sqlite_mod.bootstrap_db()
    sqlite_mod.bootstrap_memory_table({
        "strategy_doc": "test strategy",
        "watchlist": "test watchlist",
        "performance_snapshot": "{}",
        "today_context": "test context",
        "observations": "test observations",
    })
    sqlite_mod.DB_PATH = original
    from scheduler.memory import MemoryStore
    return MemoryStore(db_path=db_path)


def _end_turn(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _tool_use(name: str, input_dict: dict, tool_id: str = "t1"):
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.input = input_dict
    block.id = tool_id
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


def test_run_session_returns_text_on_end_turn(mem_db):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _end_turn('{"session": "pre_market"}')
    mock_digester = MagicMock()
    mock_digester.summarize.return_value = ""

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mem_db.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mem_db,
    )
    result = agent.run_session("pre_market", "SESSION: pre_market | DATE: 2026-04-17")
    assert result == '{"session": "pre_market"}'


def test_run_session_executes_tool_and_continues(mem_db):
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        _tool_use("trade_query", {"sql": "SELECT COUNT(*) FROM trades"}),
        _end_turn('{"session": "health_check"}'),
    ]
    mock_digester = MagicMock()
    mock_digester.summarize.return_value = ""

    from scheduler.agent import AgentCore, _execute_tool
    agent = AgentCore(
        db_path=mem_db.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mem_db,
    )

    with patch("scheduler.agent._execute_tool", return_value=[{"count": 0}]) as mock_exec:
        result = agent.run_session("health_check", "SESSION: health_check")

    assert mock_client.messages.create.call_count == 2
    mock_exec.assert_called_once_with("trade_query", {"sql": "SELECT COUNT(*) FROM trades"})
    assert "health_check" in result


def test_run_session_logs_response_and_triggers_digest(mem_db):
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _end_turn("response text")
    mock_digester = MagicMock()
    mock_digester.summarize.return_value = "a digest"

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mem_db.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mem_db,
    )
    agent.run_session("pre_market", "prompt")
    time.sleep(0.3)  # background thread
    digests = mem_db.get_recent_digests(n=5)
    assert len(digests) == 1
    assert digests[0]["digest"] == "a digest"


def test_update_memory_block_writes_to_store(mem_db):
    mock_client = MagicMock()
    mock_digester = MagicMock()

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mem_db.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mem_db,
    )
    agent.update_memory_block("strategy_doc", "new strategy text")
    assert mem_db.read("strategy_doc") == "new strategy text"


def test_get_memory_block_reads_from_store(mem_db):
    mock_client = MagicMock()
    mock_digester = MagicMock()

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mem_db.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mem_db,
    )
    assert agent.get_memory_block("watchlist") == "test watchlist"


def test_build_system_prompt_cache_control_placement(mem_db):
    from scheduler.agent import build_system_prompt
    blocks = {
        "strategy_doc": "doc", "watchlist": "wl",
        "performance_snapshot": "{}", "today_context": "ctx", "observations": "obs"
    }
    system = build_system_prompt(blocks)
    assert isinstance(system, list)
    assert len(system) == 2
    assert system[0].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in system[1]
    assert "doc" in system[1]["text"]
    assert "wl" in system[1]["text"]
    assert "ctx" in system[1]["text"]
    assert "obs" in system[1]["text"]


def test_static_prompt_contains_key_trading_constraints():
    from scheduler.agent import STATIC_PROMPT
    assert "trade_open" in STATIC_PROMPT
    assert "BEFORE" in STATIC_PROMPT or "before" in STATIC_PROMPT  # trade_open before place_order
    assert "proposed_change" in STATIC_PROMPT
    assert "filter_sql" in STATIC_PROMPT
    assert "context_json" in STATIC_PROMPT
    assert "eod_reflection" in STATIC_PROMPT
    assert "weekly_review" in STATIC_PROMPT


def test_tool_schemas_covers_all_tools():
    from scheduler.agent import TOOL_SCHEMAS
    names = {t["name"] for t in TOOL_SCHEMAS}
    required = {
        "trade_open", "trade_close", "hypothesis_log", "trade_query",
        "alpaca_get_account", "alpaca_get_positions", "alpaca_place_order",
        "alpaca_list_orders", "alpaca_cancel_order",
        "fmp_screener", "fmp_ta", "fmp_check_current_price", "fmp_news", "fmp_earnings_calendar",
        "serper_search", "run_script", "update_memory_block",
    }
    assert required == names


def test_tool_schemas_have_required_fields():
    from scheduler.agent import TOOL_SCHEMAS
    for tool in TOOL_SCHEMAS:
        assert "name" in tool
        assert "description" in tool
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]
        assert "required" in tool["input_schema"]


def test_run_session_update_memory_block_tool_writes_to_store(mem_db):
    """update_memory_block tool call in run_session must persist to MemoryStore."""
    import threading
    mock_digester = MagicMock()
    mock_digester.summarize.return_value = None
    mock_client = MagicMock()

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mem_db.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mem_db,
    )

    # Simulate: first call returns update_memory_block tool_use, second returns end_turn
    mock_client.messages.create.side_effect = [
        _tool_use("update_memory_block", {"block_name": "watchlist", "value": "NVDA | momentum long | HIGH"}, "t1"),
        _end_turn('{"session": "pre_market", "summary": "done", "errors": []}'),
    ]

    agent.run_session("pre_market", "run session")

    # The memory block must have been updated
    assert mem_db.read("watchlist") == "NVDA | momentum long | HIGH"


def test_run_session_update_memory_block_rejects_strategy_doc(mem_db):
    """update_memory_block must NOT allow writing strategy_doc directly."""
    mock_digester = MagicMock()
    mock_digester.summarize.return_value = None
    mock_client = MagicMock()

    from scheduler.agent import AgentCore
    agent = AgentCore(
        db_path=mem_db.db_path,
        model="claude-sonnet-4-6",
        api_key="test",
        _client=mock_client,
        _digester=mock_digester,
        _memory=mem_db,
    )

    mock_client.messages.create.side_effect = [
        _tool_use("update_memory_block", {"block_name": "strategy_doc", "value": "hacked"}, "t1"),
        _end_turn('{"session": "test", "summary": "done", "errors": []}'),
    ]

    agent.run_session("test", "run session")

    # strategy_doc must NOT have been overwritten
    assert mem_db.read("strategy_doc") == "test strategy"
