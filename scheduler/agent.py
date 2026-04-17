# scheduler/agent.py
"""Direct Anthropic SDK agent core. Replaces LettaTraderAgent."""
import json
import logging
import os
import threading
from datetime import date
from typing import Optional

from scheduler.memory import MemoryStore
from scheduler.digester import SessionDigester

log = logging.getLogger(__name__)

STATIC_PROMPT = """\
# ClaudeTrading Operations Manual

## Who You Are

You are the intelligence engine for an autonomous AI trading system managing a $50,000 Alpaca paper trading account. You are not an assistant — you are an active fund manager. Every session you analyze market conditions, make execution decisions, record your reasoning, and evolve your strategy through structured self-reflection.

You have full accountability for outcomes. No human approves individual trades. Your edge must be measurable, reproducible, and improving.

## Account Parameters

Starting equity: $50,000 paper account via Alpaca Markets
Universe: US equities only — no options, futures, crypto, or foreign securities
Directions: Long (buy) and short (sell) both available
Sessions: Fixed cron schedule — you cannot self-trigger

Hard position limits (defaults — evolvable via strategy gate):
- Max open positions: 5 simultaneously
- Max single position size: 15% of equity
- Risk per trade: 1% of equity
- Stop loss default: 1.5x ATR below entry (long) / above entry (short)
- Profit target default: 3x ATR from entry (minimum 2:1 R:R required)
- Max daily loss: 3% of equity — halt new positions if breached, no exceptions

Position sizing:
  shares = (equity * risk_per_trade_pct) / abs(entry_price - stop_loss)
  position_value = shares * entry_price
  Reject if position_value > equity * max_position_pct

## Memory System

You maintain five persistent memory blocks injected at session start under "Your Current State."

strategy_doc: Your trading rulebook. NEVER write to it directly. Changes via proposed_change only.
watchlist: Current candidates. Max 12 entries. Format: TICKER | thesis | date | confidence | entry zone | stop | target
performance_snapshot: JSON with win_rate_10, win_rate_20, avg_rr, equity, drawdown, pivot_alerts. Refresh from trade_query at EOD only.
today_context: Pre-market analysis. Written in pre_market. Read in market_open. Reset each day.
observations: Rolling field notes. Max 15 bullets. Format: [YYYY-MM-DD] Text in <=15 words.

## Session Responsibilities

### pre_market (6:00 AM ET)
1. alpaca_get_account — verify equity
2. fmp_ohlcv("SPY") + fmp_ohlcv("VIX") — payload includes regime signals (ADX, EMA alignment, ATR regime). Can still use run_script with market_regime_detector for cross-asset breadth if needed.
3. fmp_screener to find candidates (volume > 1M, mkt_cap > 2B)
4. For each candidate: fmp_ohlcv(ticker) — all indicators pre-calculated. Add fmp_news, fmp_earnings_calendar for qualitative context.
5. Write today_context with regime + top 5-10 setups
6. Write watchlist (max 12)
7. hypothesis_log new theses as "formed"

### market_open (9:30 AM ET)
1. Review today_context and watchlist
2. Check recent_context for live positions — skip already-held tickers
3. For each trade where conditions are met:
   a. Compute shares via sizing formula
   b. trade_open(...) FIRST — get trade_id
   c. alpaca_place_order(...)
   d. hypothesis_log(id, "testing", f"Opened trade_id {trade_id} at {entry}")
4. Skip trades where price is outside entry zone — do not chase

CRITICAL: trade_open MUST be called BEFORE alpaca_place_order. If the order fails after trade_open succeeds, call trade_close(trade_id, 0, "order_failed", 0, 0) immediately to prevent an orphaned open record. If trade_open fails, do not place the order.
No proposed_change in market_open — system rejects it.

### health_check (1:00 PM ET)
1. Review positions from recent_context
2. For each position: is the thesis still intact?
3. Close if: stop hit, thesis invalidated by news/structure, or cannot state why trade is still valid
   Close sequence: alpaca_place_order -> trade_close -> hypothesis_log update
4. Seek new setups only if buying_power > 0 AND positions < 5 AND clear setup
No proposed_change in health_check — system rejects it.

### eod_reflection (3:45 PM ET)
1. Close remaining open positions (unless overnight hold explicitly justified in today_context)
2. trade_query to compute win_rate_10, win_rate_20, avg_rr — update performance_snapshot
3. Write new observations (<=15 words, date-tagged)
4. If pattern across >=3 trades: emit proposed_change
5. Reset today_context to "Cleared."

### weekly_review (6:00 PM Sunday)
1. Comprehensive trade_query: win rates by setup_type, regime, VIX range, hypothesis
2. Confirm (>=10 trades, positive avg_r) or reject (negative avg_r) hypotheses
3. Compress observations to <=10 bullets
4. Compress watchlist — remove expired theses
5. Update performance_snapshot
6. proposed_change if major pattern found

## Tool Reference

### Market Data
fmp_screener(market_cap_more_than, volume_more_than, exchange, limit)
fmp_ohlcv(ticker, limit=5) — returns full TA payload (indicators, ICs, Alpha101). limit=raw candles exposed only.
fmp_news(tickers, limit=10)
fmp_earnings_calendar(from_date, to_date)
serper_search(query)

### Code Execution
run_script(code, timeout=30, scripts_dir="/app/scripts")
- NO API credentials inside scripts — pre-fetch data with fmp_ohlcv first
- Embed fetched data as Python variables in the script string
- End scripts with: print(json.dumps(result))

Indicator scripts (/app/scripts/indicators/):
  rsi.py -> compute_rsi(closes, period=14) -> {rsi, oversold, overbought}
  macd.py -> compute_macd(closes) -> {macd, signal, histogram, crossover}
  rate_of_change.py -> compute_roc(closes, period=10) -> {roc}
  ema_crossover.py -> compute_ema_crossover(closes, fast=9, slow=21) -> {ema_fast, ema_slow, cross}
  adx_trend_strength.py -> compute_adx(highs, lows, closes, period=14) -> {adx, trend_strength}
  supertrend.py -> compute_supertrend(highs, lows, closes) -> {direction, level}
  atr.py -> compute_atr(highs, lows, closes, period=14) -> {atr, atr_pct}
  bollinger_bands.py -> compute_bb(closes) -> {upper, middle, lower, width, pct_b}
  vix_percentile.py -> compute_vix_percentile(vix_closes) -> {percentile, regime}
  vwap.py -> compute_vwap(highs, lows, closes, volumes) -> {vwap, distance_pct}
  obv.py -> compute_obv(closes, volumes) -> {obv, trend}
  volume_profile.py -> compute_volume_profile(closes, volumes) -> {poc, value_area_high, value_area_low}
  market_regime_detector.py -> detect_regime(spy_ohlcv, vix_ohlcv) -> {regime, vix_percentile, breadth, trend_slope}
  relative_strength_scanner.py -> scan_rs(ticker_ohlcv_dict, benchmark_ohlcv) -> {rankings}

### Execution
alpaca_get_account()
alpaca_get_positions()
alpaca_place_order(symbol, qty, side, order_type="market", time_in_force="day", limit_price=None, stop_price=None)
alpaca_list_orders(status="open", limit=50)
alpaca_cancel_order(order_id)

### Record Keeping (Required)
trade_open(ticker, side, entry_price, size, setup_type, hypothesis_id, rationale,
           vix_at_entry, regime, stop_loss=None, take_profit=None, context_json=None)
  context_json must be a JSON string with indicator values at entry:
  {"rsi": 63.2, "adx": 28.1, "atr": 3.45, "atr_pct": 0.034, "volume_ratio": 1.8,
   "vix_percentile": 42.0, "macd_histogram": 0.23, "distance_from_vwap_pct": 0.012,
   "supertrend_direction": "up"}
  Include any indicator you actually computed. These values are queryable via filter_sql.

trade_close(trade_id, exit_price, exit_reason, outcome_pnl, r_multiple)
  r_multiple = outcome_pnl / (abs(entry_price - stop_loss) * size)
  exit_reason: hit_target | stop_hit | thesis_invalidated | time_exit | manual | order_failed

hypothesis_log(hypothesis_id, event_type, body)
  event_type: formed | testing | confirmed | rejected | refined
  IDs: H001, H002, H003... never reuse

trade_query(sql) — SELECT only. Blocked: INSERT UPDATE DELETE DROP ALTER CREATE PRAGMA

Useful queries:
  SELECT setup_type, COUNT(*) n, AVG(r_multiple) avg_r,
         SUM(CASE WHEN r_multiple > 0 THEN 1 ELSE 0 END)*1.0/COUNT(*) win_rate
  FROM trades WHERE closed_at IS NOT NULL GROUP BY setup_type ORDER BY avg_r DESC;

  SELECT ticker, side, setup_type, r_multiple, exit_reason, closed_at
  FROM trades WHERE closed_at IS NOT NULL ORDER BY closed_at DESC LIMIT 10;

## Strategy Evolution Gate

Valid only in: eod_reflection, weekly_review.
Invalid in: pre_market, market_open, health_check — system ignores proposed_change there.
One change at a time — check recent_context for current probationary status first.

proposed_change format:
  "proposed_change": {
    "description": "What is changing and why, referencing trade data",
    "new_strategy_doc": "Full strategy_doc replacement text — complete document",
    "filter_sql": "WHERE clause FRAGMENT only — no WHERE keyword, no SELECT, no LIMIT"
  }

filter_sql rules:
  Valid:   setup_type = 'momentum' AND json_extract(context_json, '$.rsi') < 65
  Valid:   vix_at_entry < 25 AND regime != 'bear_high_vol'
  Invalid: WHERE setup_type = 'momentum'     <- contains WHERE
  Invalid: SELECT * FROM trades WHERE ...    <- full SQL statement
  Omit filter_sql for qualitative changes.

## Risk Management

Daily halt: If today's closed trade P&L sum < -3% equity, stop opening new positions.
Every trade must have a defined stop before entry — no exceptions.
Health check: If you cannot state in one sentence why a position is still valid, close it.
Overnight: Default close before 3:50 PM ET. To hold overnight, write explicit justification in today_context.
Correlation: Max 2 positions in same sector simultaneously.

## JSON Response Format

Every session response must contain a valid JSON object:
{
  "session": "session_name",
  "date": "YYYY-MM-DD",
  "summary": "One paragraph summary of decisions and reasoning",
  "actions_taken": ["list of actions"],
  "proposed_change": null,
  "errors": []
}

For market_open, include:
  "trades_opened": [{"ticker": "X", "trade_id": N, "side": "buy", "size": N, "entry": N, "stop": N, "target": N}]
  "trades_skipped": [{"ticker": "X", "reason": "..."}]

For eod_reflection, include:
  "performance_update": {"win_rate_10": N, "avg_rr": N, "current_equity": N}

Errors go in errors[] — scheduler forwards non-empty errors to Telegram.

## Hard Constraints

- trade_query is read-only: INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/PRAGMA blocked
- run_script has no API credentials: do not call FMP/Alpaca/Serper inside scripts
- run_script: 30s timeout, 256MB RAM
- API calls: 30s timeout
- Strategy gate backtest: 60 days maximum
- One proposed_change in probation at a time
- proposed_change processed only in eod_reflection and weekly_review
- This system is stateless between sessions — no conversation history carries over
- You cannot self-trigger sessions or schedule future actions

## System Constraints

Fixed limits (non-overridable via strategy gate):
- API calls: 30s timeout per request
- run_script: 60s/512MB per execution
- Strategy gate backtest window: 60 days
- Maximum probationary changes active: 1
"""

TOOL_SCHEMAS = [
    {
        "name": "trade_open",
        "description": "Record a new trade at entry time. Call BEFORE placing the Alpaca order. Returns trade_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "entry_price": {"type": "number"},
                "size": {"type": "number"},
                "setup_type": {"type": "string"},
                "hypothesis_id": {"type": "string"},
                "rationale": {"type": "string"},
                "vix_at_entry": {"type": "number"},
                "regime": {"type": "string"},
                "stop_loss": {"type": "number"},
                "take_profit": {"type": "number"},
                "context_json": {"type": "string"},
            },
            "required": ["ticker", "side", "entry_price", "size", "setup_type",
                         "hypothesis_id", "rationale", "vix_at_entry", "regime"],
        },
    },
    {
        "name": "trade_close",
        "description": "Stamp exit fields onto an open trade after the exit order fills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "trade_id": {"type": "integer"},
                "exit_price": {"type": "number"},
                "exit_reason": {"type": "string", "enum": ["hit_target", "stop_hit", "thesis_invalidated", "time_exit", "manual", "order_failed"]},
                "outcome_pnl": {"type": "number"},
                "r_multiple": {"type": "number"},
            },
            "required": ["trade_id", "exit_price", "exit_reason", "outcome_pnl", "r_multiple"],
        },
    },
    {
        "name": "hypothesis_log",
        "description": "Append a lifecycle event to the hypothesis ledger.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis_id": {"type": "string"},
                "event_type": {"type": "string", "enum": ["formed", "testing", "confirmed", "rejected", "refined"]},
                "body": {"type": "string"},
            },
            "required": ["hypothesis_id", "event_type", "body"],
        },
    },
    {
        "name": "trade_query",
        "description": "Execute a read-only SELECT query against the trades and hypothesis_log tables.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string"},
            },
            "required": ["sql"],
        },
    },
    {
        "name": "alpaca_get_account",
        "description": "Get Alpaca account information: equity, buying_power, cash.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "alpaca_get_positions",
        "description": "Get all open positions with symbol, qty, avg_entry_price, unrealized_pl.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "alpaca_place_order",
        "description": "Place a buy or sell order. Call trade_open FIRST to get trade_id.",
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "qty": {"type": "number"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "order_type": {"type": "string", "enum": ["market", "limit", "stop", "stop_limit"]},
                "time_in_force": {"type": "string", "enum": ["day", "gtc", "opg", "cls", "ioc", "fok"]},
                "limit_price": {"type": "number"},
                "stop_price": {"type": "number"},
            },
            "required": ["symbol", "qty", "side"],
        },
    },
    {
        "name": "alpaca_list_orders",
        "description": "List orders by status. Use to confirm limit order fills.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["open", "closed", "all"]},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "name": "alpaca_cancel_order",
        "description": "Cancel an open order by its UUID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "fmp_screener",
        "description": "Screen US stocks by market cap and volume.",
        "input_schema": {
            "type": "object",
            "properties": {
                "market_cap_more_than": {"type": "integer"},
                "volume_more_than": {"type": "integer"},
                "exchange": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "name": "fmp_ohlcv",
        "description": (
            "Get a pre-calculated professional TA payload for a ticker (1D and 1W). "
            "Returns: meta (symbol, as_of, price), ohlcv_1d/1w (last `limit` candles, default 5), "
            "momentum_1d (rsi_7/14/21 each with cur+7d/14d/30d/90d hi/lo/avg; macd with crossover+divergence; stoch_5/stoch_14 with k/d/zone/crossover; mfi with divergence), "
            "trend_1d (ema_samples every-5-candles for ema21/55/89 + alignment + price_vs_ema_pct; adx/di_plus/di_minus/trend_strength; vwap/slope/price_vs_vwap_pct), "
            "trend_1w (ema_samples ema21/55; adx/trend_strength), "
            "volatility_1d (atr/atr_pct/atr_regime; bollinger upper/mid/lower 1sd+2sd + pct_b + bandwidth + squeeze bool), "
            "volume_1d (vol_ratio_1d/1w + 10d hi/lo; obv slope/vs_price/trend_days), "
            "price_structure (sr_1d: 3 support + 3 resistance with price/strength/last_tested; sr_1w: 2+2; pivot_1d: pp/r1/r2/s1/s2; wk52: hi/lo/pct/dist), "
            "ics_1d (order_blocks max 3 with type/date/ob_high/ob_low/ob_mid/tested/broken/stale; fvgs max 3; liquidity_levels max 4; market_structure with structure/last_hh/last_hl/msb; breaker_blocks max 2), "
            "ics_1w (order_blocks max 2; market_structure), "
            "patterns_1d/1w (list of {pattern, date, signal} for last 5/3 candles), "
            "alpha101 (20 WorldQuant signals: a1_momentum_peak, a2_vol_accel_corr, a3_open_vol_ranked, a4_support_floor, a6_open_vol_raw, a7_vol_gated, a9_regime_5d, a10_regime_4d, a12_capitulation, a20_gap_structure, a27_vwap_participation, a31_mean_rev, a32_vwap_persist, a34_vol_squeeze, a39_low_vol_drop, a41_geo_mid_vwap, a49_accel, a50_distribution, a55_range_vol_corr, a101_bar_quality). "
            "Priority alphas: a101_bar_quality (candlestick conviction), a12_capitulation (1-day vol-spike+drop), a34_vol_squeeze (relative squeeze), a49_accel (momentum acceleration), a7_vol_gated (volume-confirmed direction)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "limit": {"type": "integer", "description": "Raw OHLCV candles to expose (default 5). Does not affect indicator calculation depth."},
            },
            "required": ["ticker"],
        },
    },
    {
        "name": "fmp_news",
        "description": "Get recent news articles for a list of tickers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tickers": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
            "required": ["tickers"],
        },
    },
    {
        "name": "fmp_earnings_calendar",
        "description": "Get scheduled earnings between two dates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string"},
                "to_date": {"type": "string"},
            },
            "required": ["from_date", "to_date"],
        },
    },
    {
        "name": "serper_search",
        "description": "Google search for news, macro context, SEC filings, analyst ratings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "run_script",
        "description": "Execute Python in a sandboxed subprocess. Pre-fetch all data before calling. No API credentials inside scripts. End with print(json.dumps(result)).",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout": {"type": "integer"},
                "scripts_dir": {"type": "string"},
            },
            "required": ["code"],
        },
    },
]


def _build_tool_functions() -> dict:
    from scheduler.tools.sqlite import trade_open, trade_close, hypothesis_log, trade_query
    from scheduler.tools.alpaca import (
        alpaca_get_account, alpaca_get_positions, alpaca_place_order,
        alpaca_list_orders, alpaca_cancel_order,
    )
    from scheduler.tools.fmp import fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar
    from scheduler.tools.serper import serper_search
    from scheduler.tools.pyexec import run_script
    return {
        "trade_open": trade_open,
        "trade_close": trade_close,
        "hypothesis_log": hypothesis_log,
        "trade_query": trade_query,
        "alpaca_get_account": alpaca_get_account,
        "alpaca_get_positions": alpaca_get_positions,
        "alpaca_place_order": alpaca_place_order,
        "alpaca_list_orders": alpaca_list_orders,
        "alpaca_cancel_order": alpaca_cancel_order,
        "fmp_screener": fmp_screener,
        "fmp_ohlcv": fmp_ohlcv,
        "fmp_news": fmp_news,
        "fmp_earnings_calendar": fmp_earnings_calendar,
        "serper_search": serper_search,
        "run_script": run_script,
    }


def _execute_tool(name: str, input_dict: dict):
    fns = _build_tool_functions()
    fn = fns.get(name)
    if fn is None:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn(**input_dict)
    except Exception as exc:
        log.error("Tool %s failed: %s", name, exc)
        return {"error": str(exc)}


def build_system_prompt(blocks: dict) -> list:
    """Two-tier system prompt: static (cached) + dynamic memory blocks (uncached)."""
    dynamic_text = (
        "## Your Current State\n"
        "[Values read from MemoryStore at session start — written back by you each session]\n\n"
        f"### STRATEGY_DOC\n{blocks.get('strategy_doc', 'Not set.')}\n\n"
        f"### WATCHLIST\n{blocks.get('watchlist', 'Not set.')}\n\n"
        f"### PERFORMANCE_SNAPSHOT\n{blocks.get('performance_snapshot', 'Not set.')}\n\n"
        f"### TODAY_CONTEXT\n{blocks.get('today_context', 'Not set.')}\n\n"
        f"### OBSERVATIONS\n{blocks.get('observations', 'Not set.')}"
    )
    return [
        {
            "type": "text",
            "text": STATIC_PROMPT,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": dynamic_text,
        },
    ]


def _extract_text(response) -> str:
    return "\n".join(
        block.text for block in response.content if hasattr(block, "text")
    )


MAX_TOOL_ITERATIONS = 25


class AgentCore:
    """Stateless session runner. Replaces LettaTraderAgent.

    Interface compatible with strategy_gate.py:
      run_session(session_name, prompt) — was send_session(prompt)
      get_memory_block(block_name) — identical
      update_memory_block(block_name, value) — identical
    """

    def __init__(
        self,
        db_path: str = "/data/trades/trades.db",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        _client=None,
        _digester=None,
        _memory: Optional[MemoryStore] = None,
    ):
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.memory = _memory or MemoryStore(db_path=db_path)

        if _client is not None:
            self.client = _client
        else:
            import anthropic
            self.client = anthropic.Anthropic(
                api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
            )

        if _digester is not None:
            self.digester = _digester
        else:
            self.digester = SessionDigester(
                api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
            )

    def run_session(self, session_name: str, user_message: str) -> str:
        blocks = self.memory.read_all()
        system = build_system_prompt(blocks)
        messages = [{"role": "user", "content": user_message}]

        for _ in range(MAX_TOOL_ITERATIONS):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                tools=TOOL_SCHEMAS,
                messages=messages,
            )

            if response.stop_reason == "end_turn":
                text = _extract_text(response)
                log_id = self.memory.log_session(
                    session_name, date.today().isoformat(), text
                )
                threading.Thread(
                    target=self._run_digest,
                    args=(log_id, session_name, text),
                    daemon=True,
                ).start()
                return text

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        result = _execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
            else:
                raise RuntimeError(f"Unexpected stop_reason: {response.stop_reason}")

        raise RuntimeError(f"Exceeded {MAX_TOOL_ITERATIONS} tool iterations in {session_name}")

    def _run_digest(self, log_id: int, session_name: str, raw_response: str) -> None:
        digest = self.digester.summarize(raw_response, session_name)
        if digest:
            self.memory.update_session_digest(log_id, digest)

    def get_memory_block(self, block_name: str) -> Optional[str]:
        return self.memory.read(block_name)

    def update_memory_block(self, block_name: str, value: str) -> None:
        self.memory.write(block_name, value)
