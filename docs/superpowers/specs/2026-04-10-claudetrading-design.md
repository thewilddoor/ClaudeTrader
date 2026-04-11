# ClaudeTrading — System Design
**Date:** 2026-04-10  
**Status:** Approved  
**Version:** 1.0

---

## 1. Overview

ClaudeTrading is an autonomous AI trading system powered by Claude (via Letta), connected to an Alpaca paper trading account ($50,000). Claude operates with full autonomy — it selects stocks, manages positions, sets risk parameters, and continuously evolves its own strategy through structured self-reflection. All values in memory are defaults Claude can reason about and override, not hard rules.

**Core principle:** Claude is the brain. The infrastructure exists to give Claude the right information at the right time and stay out of its way.

---

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        VPS                               │
│                                                          │
│  ┌─────────────┐     ┌──────────────────────────────┐   │
│  │  APScheduler│────▶│        Letta Server           │   │
│  │  (5 triggers)│    │  (stateful Claude agent)      │   │
│  └─────────────┘     │                               │   │
│                       │  Core Memory (in-context)     │   │
│                       │  Recall Memory (searchable)   │   │
│                       │  Archival Memory (long-term)  │   │
│                       └──────────┬────────────────────┘  │
│                                  │ Tools                  │
│       ┌──────────────────────────┼────────────────────┐  │
│       │               │                │              │  │
│  ┌────▼─────┐  ┌──────▼────┐  ┌───────▼───┐  ┌──────▼┐ │
│  │ Alpaca   │  │  FMP Tool │  │  Serper   │  │PyExec │ │
│  │ MCP v2   │  │(financials│  │  Tool     │  │ Tool  │ │
│  │(61 endpts│  │  + data)  │  │(news +    │  │(sandbo│ │
│  │          │  │           │  │ research) │  │  xed) │ │
│  └──────────┘  └───────────┘  └───────────┘  └───────┘ │
│                                                          │
│  ┌──────────────────────────────────────────────────┐   │
│  │  Telegram Notifier (event-driven from scheduler) │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

**Stack:**
- **Letta** — stateful agent platform with self-managing memory (formerly MemGPT)
- **Alpaca MCP Server v2** — official Alpaca MCP server, 61 endpoints, full trading/order/data access
- **APScheduler** — Python scheduler triggering sessions via dynamic prompt injection
- **FMP** — Financial Modeling Prep API for fundamentals, screener, technicals, earnings
- **Serper** — Google Search API for news, macro events, company research
- **PyExec** — sandboxed Python subprocess execution for indicator scripts and analysis (resource-limited: CPU/memory caps, no network access, restricted imports)
- **Telegram** — event-driven notifications (not a Claude tool — fired by scheduler)
- **Docker Compose** — single-command VPS deployment, auto-restart on crash

---

## 3. Session Schedule

Five recurring APScheduler triggers plus one manual bootstrap trigger (first-run only, see Section 6). Each scheduled trigger sends a minimal **dynamic prompt injection** — not full instructions, just a session type + fresh real-time context. Claude reads its memory and determines what to do. Session responsibilities live in its strategy doc, not in prompts.

```python
# Example injections

# Pre-market
"SESSION: pre_market | DATE: 2026-04-10 | MARKET_OPENS_IN: 3h30m"

# Execution
"SESSION: market_open | DATE: 2026-04-10 | TIME: 09:30 ET"

# Health check — live positions injected
"SESSION: health_check | DATE: 2026-04-10 | TIME: 13:00 ET | POSITIONS: {live_snapshot}"

# EOD — today's closed trades injected
"SESSION: eod_reflection | DATE: 2026-04-10 | TRADES_TODAY: {trade_summary}"

# Weekly
"SESSION: weekly_review | DATE: 2026-04-10 | WEEK: 15"
```

### Session 1 — Pre-Market Research `6:00 AM ET`
- Run FMP screener (market cap, volume, momentum filters based on current strategy)
- Run `market_regime_detector.py` — assess today's regime (bull/bear/range/volatile)
- Run `relative_strength_scanner.py` on candidates
- Search Serper for macro events, earnings, sector news
- Review open positions for overnight risk
- Write `TODAY_CONTEXT` block to core memory (top 5–10 candidates with thesis)
- No trades executed

### Session 2 — Market Open Execution `9:30 AM ET`
- Act on pre-market plan
- Run indicator scripts via PyExec for final confirmation on candidates
- Place entries, set stop-losses and take-profits via Alpaca MCP
- Tag every trade with a `hypothesis_id`
- Log trade rationale immediately to recall memory

### Session 3 — Mid-Day Health Check `1:00 PM ET`
- Review open positions vs. entry thesis
- Check for news/events that invalidate thesis (Serper)
- Modify or close positions if warranted
- Detect intraday pivot triggers (VIX spike, macro shock)
- Send Telegram alert if anything abnormal
- No new entries unless high-conviction setup emerges

### Session 4 — End-of-Day Reflection `3:45 PM ET`
- Review all today's trades vs. planned thesis
- Score decisions (entry timing, sizing, stop placement, exit)
- Update hypothesis trade counts; evaluate mature hypotheses (≥15 trades by default)
- Update `INDICATOR_EFFECTIVENESS` in archival memory
- Check pivot triggers — revise strategy if triggered
- Bump strategy version number if changed, archive old version
- Update watchlist confidence scores
- Send daily Telegram summary

### Session 5 — Weekly Deep Review `Sunday 6:00 PM ET`
- Mine recall memory for week-wide patterns
- Review all active hypotheses (promote, reject, extend)
- Prune watchlist (tickers with repeated failed theses)
- Compress week's trade records into archival summary (keeps recall memory lean)
- Assess whether strategy version fits current market regime
- Evolve position sizing / risk defaults based on 4-week performance
- Send weekly Telegram report

---

## 4. Memory Architecture

Letta provides three memory tiers. All values stored here are **defaults and starting points** — Claude can reason beyond them and override them at any time with good justification.

### Core Memory *(always in context, ~2000 tokens)*

```
STRATEGY_DOC (versioned)
  - Current trading philosophy
  - Active approach (e.g., momentum, mean-reversion, hybrid)
  - Entry / exit criteria
  - Position sizing defaults
  - Max open positions default, max daily loss default
  - Current market regime
  - Session responsibilities (what to do in each session)
  - Version number + last revised date + reason for revision

WATCHLIST
  - Active tickers with thesis + date added
  - Confidence score per ticker (Claude-assigned, updated over time)

PERFORMANCE_SNAPSHOT
  - Rolling 10-trade win rate
  - Rolling 20-trade win rate
  - Current drawdown from peak
  - Avg R:R ratio (rolling)
  - Active pivot alerts (if any)

TODAY_CONTEXT  (reset each pre-market)
  - Market regime assessment for today
  - Key events / news flags
  - Pre-screened candidates with thesis
  - Session intent
```

### Recall Memory *(searchable trade journal)*

Each trade is a structured record Claude can query:

```
TRADE_RECORD
  - ticker, direction, entry/exit price, size
  - setup type (what pattern triggered entry)
  - indicators used
  - thesis at entry
  - outcome: P&L, R-multiple
  - post-trade note (what happened vs. thesis)
  - hypothesis_id
  - session log reference
```

Claude queries recall memory during EOD and weekly reviews:
*"What is my win rate on momentum setups in high-VIX environments over the last 30 trades?"*

### Archival Memory *(long-term, compressed)*

```
STRATEGY_VERSIONS
  - Full history of every strategy doc revision
  - Why it changed, what triggered it
  - Performance before/after each version

HYPOTHESIS_LEDGER
  - Every hypothesis Claude has formed and tested
  - Status: forming / testing (N/M trades) / confirmed / rejected / refined
  - Evidence for each conclusion

INDICATOR_EFFECTIVENESS
  - Per-indicator performance tracking across market regimes
  - Which indicators correlate with winning trades in which conditions

SCRIPT_LIBRARY_LOG
  - History of scripts created, modified, archived
  - Performance notes

WEEKLY_REVIEWS
  - Full weekly reflection logs, archived after processing
```

---

## 5. Self-Learning & Evolution Loop

```
PRE-MARKET
  → Form or update hypotheses based on today's setup and regime
  → Check if any hypothesis has enough trades to evaluate

EXECUTION
  → Tag every trade with hypothesis_id
  → Log rationale to recall memory immediately after execution

EOD REFLECTION
  → Query recall memory for today's trades
  → Score own decisions vs. thesis
  → Update hypothesis trade counts
  → If hypothesis mature (≥15 trades by default): evaluate → accept / reject / refine
  → Update INDICATOR_EFFECTIVENESS scores
  → If strategy changes: rewrite STRATEGY_DOC, bump version, archive old version
  → Check pivot triggers

WEEKLY REVIEW
  → Deep pattern mining across recall memory
  → Review all active hypotheses
  → Prune watchlist, evolve sizing defaults based on 4-week data
  → Compress old records into archival summaries
```

### Pivot Triggers *(defaults — Claude can revise these)*

| Trigger | Default Condition | Default Response |
|---|---|---|
| Intraday | VIX > 30 or macro shock detected | Close risky positions, hold cash, log reasoning |
| Short-term | Win rate < 40% over last 10 trades | Pause new entries, run mini-reflection, revise entry criteria |
| Strategic | Win rate < 45% over last 20 trades OR drawdown > 15% | Full strategy overhaul, version bump, Telegram alert |
| Regime shift | Market regime changes (detected pre-market) | Update strategy doc with regime-appropriate approach |

---

## 6. Components

### Pre-Built Indicator Library

```
/scripts/indicators/
  index.json                        ← Claude reads this to know what's available
  trend/
    ema_crossover.py
    adx_trend_strength.py
    supertrend.py
  momentum/
    rsi.py
    macd.py
    rate_of_change.py
  volatility/
    atr.py
    bollinger_bands.py
    vix_percentile.py
  volume/
    vwap.py
    obv.py
    volume_profile.py
  composite/
    market_regime_detector.py       ← VIX + SPY trend + sector breadth
    relative_strength_scanner.py
```

Each script: takes ticker + OHLCV data as input, returns a structured JSON result. Claude pipes FMP data into them via PyExec.

Claude can create new scripts in `/scripts/analysis/`, update `index.json`, and archive scripts it stops using.

### Script Library Index (`index.json`)

```json
{
  "indicators": {
    "rsi": {
      "path": "indicators/momentum/rsi.py",
      "inputs": "ticker, period",
      "created": "2026-04-10",
      "notes": "reliable in ranging markets"
    },
    "market_regime_detector": {
      "path": "indicators/composite/market_regime_detector.py",
      "inputs": "none",
      "created": "2026-04-10",
      "notes": "run pre-market daily"
    }
  },
  "analysis": {}
}
```

### Stock Screening Flow *(runs during pre-market)*

```
FMP Screener
  → Filter: market cap > $1B, avg volume > 500k, exchange = NYSE/NASDAQ (defaults)
  → Apply strategy-relevant technical filters (evolve with strategy)

PyExec
  → relative_strength_scanner.py on candidates
  → market_regime_detector.py for today's regime

Serper
  → News check on top candidates (earnings dates, risks, catalysts)

Output
  → TODAY_CONTEXT in core memory: top 5–10 candidates with thesis
```

Screening criteria are Claude's own and evolve as its strategy evolves.

### Bootstrap Session *(first-run only, triggered manually)*

One-time initialization before any trading begins:
1. Write initial strategy doc to core memory (philosophy, starter defaults)
2. Write empty watchlist
3. Write zeroed performance snapshot
4. Load indicator library index into core memory
5. Set market regime to "unknown — assess on first pre-market"
6. Log: "Bootstrap complete."

Never runs again after first deploy.

---

## 7. Error Handling

Each session runs inside a scheduler-level try/except wrapper. Claude only sees a clean session or a tool-call failure note — it is not responsible for infrastructure recovery.

| Level | Condition | Response |
|---|---|---|
| Soft | FMP rate limit, Serper timeout | Retry with backoff (3x), continue without that data |
| Hard | Alpaca MCP down during execution | Skip new entries, hold existing positions, Telegram alert |
| Critical | Letta server crash | Docker auto-restarts; scheduler retries session after 60s; Telegram alert |
| Safety | Order rejected by Alpaca | Log rejection reason, skip that trade, continue |
| Timeout | Session runs > 15 min | Force-stop, log truncation, Telegram alert |

---

## 8. Telegram Notifications

Event-driven from the scheduler. Not a Claude tool — Claude does not call Telegram directly. At the end of each session, Claude emits a structured JSON summary as its final message (e.g., trades executed, lesson learned, strategy change). The scheduler parses this output and formats it into Telegram messages. Error notifications are fired by the scheduler's try/except wrapper independently of Claude.

```
📈 TRADE EXECUTED
NVDA | LONG | 47 shares @ $891.20
Stop: $872.00 | Target: $945.00
Risk: 1.8% | Session: market_open

⚠️ HEALTH CHECK ALERT
TSLA position: thesis invalidated
Action: closed @ $187.40 | -0.9R

📊 EOD SUMMARY — Apr 10
Trades: 3 | P&L: +$1,240
Win rate (10): 60% | Avg R:R: 1.8
Strategy: v4 (unchanged)
Lesson: [Claude's own words]

🔄 STRATEGY UPDATED → v5
Trigger: 20-trade win rate dropped to 42%
Change: tightened entry criteria for low-volume setups
[Claude's diagnostic note]

❌ SESSION ERROR
eod_reflection failed: Letta timeout
Action: retrying in 60s
```

---

## 9. Deployment

### Docker Compose Services

```yaml
services:
  letta:        # Letta server — persistent agent state + memory
  alpaca-mcp:   # Alpaca official MCP server v2
  scheduler:    # Python APScheduler + session triggers + Telegram

volumes:
  letta-db      # Letta memory database
  scripts       # Indicator library + Claude-created scripts
  logs          # Session and error logs
```

### Environment Variables (`.env` — never in code)

```
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
FMP_API_KEY=
SERPER_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
LETTA_SERVER_URL=http://letta:8283
```

### Directory Structure

```
/opt/claudetrading/
  docker-compose.yml
  .env
  scheduler/
    main.py           ← APScheduler + session triggers
    sessions.py       ← dynamic prompt injection builders
    notifier.py       ← Telegram dispatcher
    tools/
      fmp.py          ← FMP HTTP wrapper (registered as Letta tool)
      serper.py       ← Serper wrapper
      pyexec.py       ← sandboxed Python execution tool
  scripts/
    indicators/       ← pre-built indicator library
    analysis/         ← Claude-created scripts land here
    index.json        ← script library index
  logs/
    sessions/
    errors/
  docs/
    superpowers/
      specs/
```

### Market Timeframe
Claude trades on the **1D timeframe**. All technical analysis and indicator scripts operate on daily OHLCV data.

---

## 10. Key Design Decisions

| Decision | Choice | Reason |
|---|---|---|
| Agent framework | Letta | Purpose-built for stateful, self-learning agents with OS-inspired memory management |
| Trading API | Alpaca MCP Server v2 | Official, 61 endpoints, works natively with Claude, no custom integration needed |
| Prompt style | Dynamic injection | Minimal per-session context; Claude's behavior lives in its own memory, not prompts |
| Memory values | Defaults, not rules | Claude reasons about and can override any value — enables genuine autonomy and evolution |
| Script execution | PyExec (sandboxed) | Claude can create, run, and persist its own analysis tools |
| Stock universe | Full US market (default) | Claude evolves its own focus area over time |
| Timeframe | 1D | Reduces noise, suits scheduled (non-HFT) operation |
| Deployment | Docker Compose | Single-command start, auto-restart, secrets in .env |
