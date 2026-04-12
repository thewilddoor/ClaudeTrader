# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

ClaudeTrading is an autonomous AI trading system where Claude (via Letta) acts as the intelligence engine for a $50k Alpaca paper trading account. Claude selects stocks, manages positions, sets risk parameters, and evolves its own strategy through structured self-reflection. The infrastructure exists to give Claude the right information at the right time — all memory values are defaults Claude can override, not hard rules.

## Commands

### Running Locally (Development)

```bash
source .venv/bin/activate        # activate venv (already configured)
pip install -r requirements.txt  # install deps

python -m scheduler.bootstrap    # one-time: create agent, register tools, init DB
python -m scheduler.main         # run the scheduler
```

### Docker

```bash
docker-compose up -d             # start all 3 services (Letta, Alpaca MCP, Scheduler)
docker-compose logs -f scheduler # tail scheduler logs
docker-compose down              # stop
```

### Tests

```bash
pytest                           # run all tests
pytest tests/test_agent.py       # single file
pytest -v                        # verbose output
pytest --cov=scheduler           # with coverage
```

Tests use `tests/conftest.py` which auto-injects dummy env vars — no real API keys needed.

## Architecture

### Three-Service Docker Composition

```
Letta (port 8283)    — Stateful Claude agent platform, holds 4 memory blocks
Alpaca MCP (port 8000) — Official Alpaca REST API MCP server
Scheduler            — APScheduler with 5 cron jobs + 1 nightly backup
```

Three Docker volumes: `letta-db` (agent memory), `agent-state` (`.agent_id` file), `trades-db` (SQLite at `/data/trades/trades.db`).

### Session Schedule (Eastern Time)

| Time | Session | Key Action |
|------|---------|------------|
| 6:00 AM | `pre_market` | Screen stocks, detect market regime, update watchlist + today_context |
| 9:30 AM | `market_open` | Execute planned trades, call `trade_open()` per trade |
| 1:00 PM | `health_check` | Monitor positions, close if thesis invalidated |
| 3:45 PM | `eod_reflection` | Review P&L, update performance_snapshot, evolve strategy_doc |
| 6:00 PM Sun | `weekly_review` | Deep pattern mining, compress memory |
| 2:00 AM daily | backup | WAL-safe SQLite copy |

Each session: `sessions.py` builds a dynamic prompt → `agent.py` sends to Letta → Claude reasons with tools → scheduler parses JSON response → `notifier.py` sends Telegram alert on trades/errors.

### Letta Agent Memory (4 Blocks, All In-Context)

- `strategy_doc` (4000 chars) — trading rules, position sizing, session responsibilities, trade record instructions
- `watchlist` (2000 chars) — top candidates with thesis + confidence
- `performance_snapshot` (1000 chars) — JSON: win_rate, avg_r_multiple, equity, drawdown
- `today_context` (2000 chars) — pre-market analysis output + 5–10 candidates

Claude reads and rewrites these directly during sessions. The scheduler injects live data (positions, today's trades) into health_check and eod_reflection prompts before sending.

### 11 Registered Tools

Tools are Python functions registered with Letta via `scheduler/tools/registry.py`. They are NOT MCP tools (except Alpaca MCP which is attached separately).

**SQLite (`scheduler/tools/sqlite.py`):**
- `trade_open(...)` → returns `trade_id`
- `trade_close(trade_id, exit_price, exit_reason, outcome_pnl, r_multiple)`
- `hypothesis_log(hypothesis_id, event_type, body)` — lifecycle events
- `trade_query(sql)` — read-only SELECT only; blocks DDL/DML

**Alpaca (`scheduler/tools/alpaca.py`):** Direct HTTP to Alpaca API (not via MCP). `get_account`, `get_positions`, `place_order`, `list_orders`, `cancel_order`.

**FMP (`scheduler/tools/fmp.py`):** `fmp_screener`, `fmp_ohlcv` (90 days OHLCV), `fmp_news`, `fmp_earnings_calendar`.

**Research (`scheduler/tools/serper.py`):** `serper_search` — Google Search API.

**Code execution (`scheduler/tools/pyexec.py`):** `run_script(code, timeout, scripts_dir)` — sandboxed subprocess, 256MB memory limit, 30s timeout, PYTHONPATH includes `/app/scripts`.

### Indicator Scripts (`scripts/indicators/`)

15 scripts organized by category: momentum (RSI, MACD, ROC), trend (EMA crossover, ADX, Supertrend), volatility (ATR, Bollinger Bands, VIX percentile), volume (VWAP, OBV, Volume Profile), composite (Market Regime Detector, Relative Strength Scanner).

Claude executes them via `run_script()`. All are indexed in `scripts/indicators/index.json`.

### Bootstrap vs Runtime

`bootstrap.py` runs once (or on restart) and:
1. Creates SQLite schema (`trades` + `hypothesis_log` tables)
2. Creates the Letta agent with 4 memory blocks if it doesn't exist
3. Re-registers all 11 tools on every startup (not just first time)
4. Attempts to attach Alpaca MCP server (gracefully skips on network failure)
5. Saves agent_id to `/app/state/.agent_id`

`main.py` reads the saved agent_id and wires up APScheduler.

### Deployment

The Docker stack runs on the **VPS**, not locally. Local machine is for dev and tests only.
To run a one-time agent update after a feature merge:
```bash
ssh vps "cd ~/ClaudeTrading && docker-compose exec scheduler python scripts/one_time/<script>.py"
```

One-time update scripts live in `scripts/one_time/`. Pattern: read current state → patch memory block if needed → send a `send_session` message explaining the change to the running agent.

### Strategy Change Gate (`scheduler/strategy_gate.py`)

Claude must not write `strategy_doc` directly. Strategy changes go through the gate:
- Claude emits `proposed_change` in session JSON; scheduler intercepts it in `run_session`
- Backtestable changes (with `filter_sql`) are pre-screened against 60 days of closed trades
- All non-blocked changes enter probation; auto-promote after 10 trades (backtested) or 20 (qualitative)
- Auto-revert if win rate drops >15pp or avg-R drops >0.5 from baseline
- `strategy_versions` table tracks every version with status (`confirmed`/`probationary`/`reverted`) and full `doc_text` for revert restoration
- Pending feedback accumulates in `/app/state/pending_feedback.txt` (append-only); read-and-cleared at start of next EOD/weekly session

### Key Design Constraints

- `Optional[float]` must be used for nullable floats in tool signatures — Letta's schema generator is incompatible with `float | None` union syntax
- `trade_query` enforces read-only by checking for forbidden SQL keywords — don't tighten this to regex as it blocks legitimate nested queries
- The `notifier.py` Telegram alerts are triggered by the scheduler parsing Claude's JSON response, not by Claude calling a tool directly
- Letta writes must precede DB commits — `apply_change` and `check_probation` both write to Letta before inserting/updating `strategy_versions` rows; a failed Letta write must leave no phantom DB row
- `trade_open` accepts optional `context_json: Optional[str]` — a JSON blob of indicator values (rsi, adx, atr, volume_ratio, etc.) at entry time; stamped automatically with the current `strategy_version`
