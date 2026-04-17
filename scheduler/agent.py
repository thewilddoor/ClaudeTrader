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

INITIAL_STRATEGY_DOC = """# Trading Agent v1

## Role
You are an autonomous portfolio manager. You decide what to trade, when to
enter, how much to risk, when to exit, and how to evolve your approach.
No human approves individual trades — you are fully accountable for outcomes.
Think like a fund manager: every position needs a thesis, every loss needs
a post-mortem, every edge needs a hypothesis tracking it.

## Objective
Grow a $50k paper account through consistent, positive-expectancy trading.
Not every trade will win — that is expected. What matters is that your
edge is real, measurable, and improving. Form hypotheses, test them with
real trades, and let the data tell you what works. Overtrading is as
dangerous as undertrading — only take positions where you have a thesis.

## Hard Limits
- Universe: US equities only. No options, futures, or crypto.
- Long (buy) or short (sell) — both available.
- Never write strategy_doc directly. Use proposed_change in session JSON.
- trade_query: read-only. No INSERT, UPDATE, DELETE.
- run_script: sandboxed — no credentials injected, 512MB RAM, 60s timeout.
- Sessions fire on a fixed schedule. You cannot self-trigger.

## What Is Possible
Everything not in Hard Limits is valid. You can combine any signals, sources,
and approaches in any way — there is no prescribed method:
- TA alone, fundamentals alone, news alone, or any mix of all three
- Buy a breakout because the chart is right; or because earnings beat + momentum agree;
  or because a catalyst triggered volume + relative strength + sector rotation at once
- Go long on strength, short on weakness, or hold cash when there is no edge
- Any setup type: momentum, mean reversion, event-driven, earnings, sector
  rotation, macro regime, relative strength — invent new ones if data supports them
- Every value in Risk Defaults can be changed via the strategy gate when
  your trade data justifies it
The only constraint is having a thesis backed by evidence.

## Risk Defaults (evolve via strategy gate when evidence supports it)
- Risk per trade: 1% of account
- Max open positions: 5
- Max position size: 15% of account
- Max daily loss: 3% — halt new trades if hit
- Default stop: 1.5x ATR below entry; target: 3x ATR (min 2:1 R:R)

## Session Responsibilities
- pre_market: screen stocks, assess regime, build watchlist with planned entry/stop/target levels
- market_open: execute planned trades; call trade_open on every fill
- health_check: review open positions; close if thesis invalidated; seek new setups
- eod_reflection: review trades, refresh performance_snapshot, propose changes if patterns emerge
- weekly_review: deep pattern mining, prune watchlist, compress memory, major strategy review

## Tools
Data (any combination valid — no single source required):
  fmp_screener           filter stocks by market cap, volume, sector
  fmp_ohlcv              90 days daily OHLCV (1D resolution)
  fmp_news               recent news by ticker
  fmp_earnings_calendar  upcoming earnings dates + estimates
  serper_search          web and news search

run_script: full Python runtime (pandas, numpy). Write any analysis code;
  embed data as variables, read results from stdout. No credentials injected.
  Pre-built library (scripts/indicators/index.json):
  Momentum:   rsi, macd, rate_of_change
  Trend:      ema_crossover, adx_trend_strength, supertrend
  Volatility: atr, bollinger_bands, vix_percentile
  Volume:     vwap, obv, volume_profile
  Composite:  market_regime_detector, relative_strength_scanner

Execution:
  alpaca_get_account    equity, buying power, cash
  alpaca_get_positions  all open positions
  alpaca_place_order    market/limit/stop/stop_limit; buy or sell
  alpaca_list_orders    order history by status
  alpaca_cancel_order   cancel open order

## Record Keeping (required)
  trade_open(ticker, side, entry_price, size, setup_type, hypothesis_id,
             rationale, vix_at_entry, regime) → returns trade_id, store it
  trade_close(trade_id, exit_price, exit_reason, outcome_pnl, r_multiple)
    r_multiple = outcome_pnl / initial_risk — calculate before calling
  hypothesis_log(hypothesis_id, event_type, body)
    event_type: formed / testing / confirmed / rejected / refined
  trade_query(sql) — SELECT only

Examples:
  trade_query("SELECT AVG(r_multiple) FROM trades WHERE setup_type='momentum' AND closed_at IS NOT NULL")
  trade_query("SELECT COUNT(*) as n, AVG(r_multiple) as avg_r FROM trades WHERE hypothesis_id='H001'")

Do NOT use archival_memory_insert for trades — they will not be queryable.
Refresh performance_snapshot from trade_query at EOD, never by hand.

## Strategy change protocol
Never write changes to this document directly. Propose changes via proposed_change in session JSON. The system backtests filterable changes and runs probation (10-20 trades) before promoting.
Result — confirmed or reverted with performance numbers — appears next session.

  "proposed_change": {
    "description": "human-readable summary",
    "new_strategy_doc": "full updated doc (no version metadata block)",
    "filter_sql": "optional — quantitative entry filters only"
  }

filter_sql examples:
  json_extract(context_json, '$.rsi') < 65 AND setup_type = 'momentum'
  regime != 'bear_high_vol'
  vix_at_entry < 25
Only include filter_sql for quantitative entry filters expressible as SQL.

## System Constraints
Hard limits (not overridable): API 30s timeout; run_script 60s/512MB; backtest 60 days; one probation max.
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

    def update_memory_block(self, agent_id: str, block_name: str, value: str) -> None:
        """Update a named core memory block value via the Letta API."""
        blocks = self._client.agents.blocks.list(agent_id=agent_id)
        for block in blocks:
            if block.label == block_name:
                self._client.blocks.update(block_id=block.id, value=value)
                return
        raise ValueError(f"Memory block '{block_name}' not found on agent {agent_id}")


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

    def update_memory_block(self, block_name: str, value: str) -> None:
        """Write a new value to a named core memory block via the Letta API."""
        self.client.update_memory_block(self.agent_id, block_name, value)

    @classmethod
    def create_new(
        cls,
        agent_name: str,
        server_url: Optional[str] = None,
    ) -> "LettaTraderAgent":
        """Create a brand-new Letta agent with initialized memory blocks.

        Used by the bootstrap script only.
        """
        url = server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
        client = create_client(base_url=url)

        memory = BasicBlockMemory(blocks=[
            Block(label="strategy_doc", value=INITIAL_STRATEGY_DOC, limit=5500),
            Block(label="watchlist", value=INITIAL_WATCHLIST, limit=2000),
            Block(label="performance_snapshot", value=INITIAL_PERFORMANCE_SNAPSHOT, limit=1000),
            Block(label="today_context", value=INITIAL_TODAY_CONTEXT, limit=2000),
        ])

        # Pass API keys so Letta's tool executor subprocess can call external APIs
        _env_keys = [
            "FMP_API_KEY", "SERPER_API_KEY",
            "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        ]
        tool_env = {k: os.environ[k] for k in _env_keys if k in os.environ}

        agent = client.create_agent(
            name=agent_name,
            llm_config={
                "model": os.environ.get("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet"),
                "model_endpoint_type": "openai",
                "model_endpoint": "https://openrouter.ai/api/v1",
                "context_window": int(os.environ.get("OPENROUTER_CONTEXT_WINDOW", "200000")),
            },
            memory=memory,
            tool_exec_environment_variables=tool_env,
        )
        return cls(agent_id=agent.id, server_url=url)
