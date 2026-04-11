from unittest.mock import MagicMock, patch
import pytest
from scheduler.agent import LettaTraderAgent


def test_agent_sends_message_and_returns_response():
    with patch("scheduler.agent.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client
        mock_response = MagicMock()
        mock_response.messages = [MagicMock(text='{"status": "ok", "trades": []}')]
        mock_client.send_message.return_value = mock_response

        agent = LettaTraderAgent(agent_id="test-agent-id")
        result = agent.send_session("SESSION: market_open | DATE: 2026-04-10")

        mock_client.send_message.assert_called_once()
        assert result is not None


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
