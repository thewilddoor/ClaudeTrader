from unittest.mock import MagicMock, Mock, patch
import pytest
from scheduler.agent import LettaTraderAgent, _LettaClientShim, INITIAL_STRATEGY_DOC


def test_agent_sends_message_and_returns_response():
    with patch("scheduler.agent.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        # letta_client AssistantMessage uses .content (str), not .text
        mock_msg = MagicMock()
        mock_msg.message_type = "assistant_message"
        mock_msg.content = '{"status": "ok", "trades": []}'
        mock_response = MagicMock()
        mock_response.messages = [mock_msg]
        mock_client.send_message.return_value = mock_response

        agent = LettaTraderAgent(agent_id="test-agent-id")
        result = agent.send_session("SESSION: market_open | DATE: 2026-04-10")

        mock_client.send_message.assert_called_once()
        assert result == '{"status": "ok", "trades": []}'


def test_agent_get_core_memory_block():
    with patch("scheduler.agent.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client
        mock_block = MagicMock()
        mock_block.value = '{"version": "v1"}'
        mock_block.label = "strategy_doc"
        mock_client.get_in_context_memory.return_value = MagicMock(blocks=[mock_block])

        agent = LettaTraderAgent(agent_id="test-agent-id")
        block = agent.get_memory_block("strategy_doc")
        assert block == '{"version": "v1"}'


def test_shim_update_memory_block_finds_block_and_calls_modify():
    mock_block = Mock()
    mock_block.label = "strategy_doc"
    mock_block.id = "block-abc"

    mock_letta = Mock()
    mock_letta.agents.blocks.list.return_value = [mock_block]

    shim = _LettaClientShim(mock_letta)
    shim.update_memory_block("agent-123", "strategy_doc", "updated text")

    mock_letta.agents.blocks.list.assert_called_once_with(agent_id="agent-123")
    mock_letta.blocks.modify.assert_called_once_with(block_id="block-abc", value="updated text")


def test_shim_update_memory_block_raises_if_block_not_found():
    mock_block = Mock()
    mock_block.label = "watchlist"
    mock_letta = Mock()
    mock_letta.agents.blocks.list.return_value = [mock_block]

    shim = _LettaClientShim(mock_letta)
    with pytest.raises(ValueError, match="strategy_doc"):
        shim.update_memory_block("agent-123", "strategy_doc", "text")


def test_agent_update_memory_block_delegates_to_shim():
    with patch("scheduler.agent.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client

        agent = LettaTraderAgent(agent_id="test-id")
        agent.update_memory_block("strategy_doc", "new value")

        mock_client.update_memory_block.assert_called_once_with(
            "test-id", "strategy_doc", "new value"
        )


def test_initial_strategy_doc_contains_protocol_instruction():
    assert "proposed_change" in INITIAL_STRATEGY_DOC
    assert "Strategy change protocol" in INITIAL_STRATEGY_DOC
    assert "Never write changes to this document directly" in INITIAL_STRATEGY_DOC
