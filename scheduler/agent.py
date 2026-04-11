"""Letta agent wrapper for the ClaudeTrading scheduler.

Provides LettaTraderAgent as the primary interface for sending session
prompts and reading core memory blocks from a running Letta server.

The module-level `create_client` function is a thin factory that returns
a compatibility shim over `letta_client.Letta`, making it easy to mock
in tests via `patch("scheduler.agent.create_client")`.
"""

import os
from typing import Optional

from letta_client import Letta
from letta.schemas.memory import BasicBlockMemory
from letta.schemas.block import Block


# ---------------------------------------------------------------------------
# Constants: initial memory block content
# ---------------------------------------------------------------------------

INITIAL_STRATEGY_DOC = """# Strategy Document v1
## Philosophy
Trade US equities on the 1D timeframe. All values below are starting defaults — override with reasoning.

## Approach
Momentum-first. Look for stocks with strong relative strength, clear trend, and volume confirmation.

## Entry Criteria (defaults)
- Price above 50-day EMA
- RSI between 40-70 (not overbought at entry)
- Volume above 20-day average
- Market regime: bull or range (not bear_high_vol)

## Exit Criteria (defaults)
- Stop loss: 1.5x ATR below entry
- Take profit: 3x ATR above entry (minimum 2:1 R:R)
- Trail stop after 1.5R profit

## Position Sizing (defaults)
- Risk per trade: 1% of account
- Max open positions: 5
- Max position size: 15% of account
- Max daily loss: 3% of account

## Session Responsibilities
- pre_market: screen stocks, assess regime, build today's watchlist and thesis
- market_open: execute planned trades, set stops and targets
- health_check: monitor open positions, check news, close if thesis invalidated
- eod_reflection: review trades, update hypotheses, evolve strategy if needed
- weekly_review: deep pattern mining, prune watchlist, compress memory

## Market Regime
unknown — assess on first pre_market session
"""

INITIAL_PERFORMANCE_SNAPSHOT = """{
  "trades_total": 0,
  "win_rate_10": null,
  "win_rate_20": null,
  "avg_rr": null,
  "current_drawdown_pct": 0.0,
  "peak_equity": 50000.0,
  "current_equity": 50000.0,
  "pivot_alerts": []
}"""

INITIAL_WATCHLIST = """# Watchlist
Empty on bootstrap — populated during pre_market sessions.
Format per entry: TICKER | thesis | date_added | confidence (1-10)
"""

INITIAL_TODAY_CONTEXT = """# Today's Context
Not yet populated — will be written during pre_market session.
"""


# ---------------------------------------------------------------------------
# Compatibility shim
# ---------------------------------------------------------------------------

class _LettaClientShim:
    """Wraps letta_client.Letta with the interface expected by LettaTraderAgent.

    Translates the new REST-style API (client.agents.messages.create,
    client.agents.blocks.list) into the familiar send_message /
    get_in_context_memory surface so that existing call sites and tests
    remain unchanged.
    """

    def __init__(self, letta_client: Letta) -> None:
        self._client = letta_client

    def send_message(self, agent_id: str, message: str, role: str = "user"):
        """Send a message to the agent and return the response."""
        response = self._client.agents.messages.create(
            agent_id=agent_id,
            messages=[{"role": role, "content": message}],
        )
        return response

    def get_in_context_memory(self, agent_id: str):
        """Return an object with a .blocks attribute listing core memory blocks."""
        blocks = self._client.agents.blocks.list(agent_id=agent_id)

        class _MemoryView:
            pass

        view = _MemoryView()
        view.blocks = list(blocks)
        return view

    def create_agent(self, name: str, llm_config, memory, **kwargs):
        """Thin pass-through used only by bootstrap (create_new classmethod)."""
        return self._client.agents.create(
            name=name,
            llm_config=llm_config,
            memory_blocks=[
                {"label": b.label, "value": b.value, "limit": b.limit}
                for b in memory.blocks
            ],
            **kwargs,
        )


def create_client(base_url: Optional[str] = None) -> _LettaClientShim:
    """Factory that returns a shim client pointing at the given Letta server URL.

    Defined at module level so that tests can mock it with:
        patch("scheduler.agent.create_client")
    """
    url = base_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
    return _LettaClientShim(Letta(base_url=url))


# ---------------------------------------------------------------------------
# Main wrapper class
# ---------------------------------------------------------------------------

class LettaTraderAgent:
    """High-level wrapper around a persistent Letta agent.

    All I/O goes through an injected (or auto-created) shim client so that
    the class is straightforward to unit-test with mocks.
    """

    def __init__(
        self,
        agent_id: str,
        server_url: Optional[str] = None,
        _client=None,  # injectable for testing
    ) -> None:
        self.agent_id = agent_id
        self.client = _client if _client is not None else create_client(
            base_url=server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
        )

    def send_session(self, prompt: str) -> str:
        """Send a session prompt to the agent and return the last assistant message text.

        letta_client AssistantMessage uses .content (str or list of content parts),
        not .text. Content parts (LettaAssistantMessageContentUnion) each have .text.
        """
        response = self.client.send_message(
            agent_id=self.agent_id,
            message=prompt,
            role="user",
        )
        texts = []
        for m in response.messages:
            if getattr(m, "message_type", None) != "assistant_message":
                continue
            content = getattr(m, "content", None)
            if isinstance(content, str) and content:
                texts.append(content)
            elif isinstance(content, list):
                for part in content:
                    text = getattr(part, "text", None)
                    if text:
                        texts.append(text)
        return texts[-1] if texts else ""

    def get_memory_block(self, block_name: str) -> Optional[str]:
        """Read a named core memory block and return its value string."""
        memory = self.client.get_in_context_memory(agent_id=self.agent_id)
        for block in memory.blocks:
            if block.label == block_name:
                return block.value
        return None

    @classmethod
    def create_new(
        cls,
        agent_name: str,
        server_url: Optional[str] = None,
    ) -> "LettaTraderAgent":
        """Create a brand-new Letta agent with initialized memory blocks.

        Used by the bootstrap script only — not covered by unit tests.
        """
        url = server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
        client = create_client(base_url=url)

        memory = BasicBlockMemory(blocks=[
            Block(label="strategy_doc", value=INITIAL_STRATEGY_DOC, limit=4000),
            Block(label="watchlist", value=INITIAL_WATCHLIST, limit=2000),
            Block(label="performance_snapshot", value=INITIAL_PERFORMANCE_SNAPSHOT, limit=1000),
            Block(label="today_context", value=INITIAL_TODAY_CONTEXT, limit=2000),
        ])

        agent = client.create_agent(
            name=agent_name,
            llm_config={
                "model": "claude-sonnet-4-6",
                "model_endpoint_type": "anthropic",
                "model_endpoint": "https://api.anthropic.com/v1",
                "context_window": 200000,
            },
            memory=memory,
        )
        return cls(agent_id=agent.id, server_url=url)
