# Design: Expose Hidden Defaults & Limits to Claude

**Date:** 2026-04-15  
**Status:** Approved

## Problem

Claude operates with tool defaults and system hard limits it has no visibility into. This causes two failure modes:
1. Claude never overrides conservative defaults (e.g. $1B market cap screener) because it doesn't know they're changeable.
2. Claude plans work that silently fails (timeouts, OOM kills) because it doesn't know the hard limits.

## Scope

### Category A — Overridable defaults (document + mark as changeable)
Tool docstring param descriptions get `(changeable)` appended wherever Claude can pass a different value.

| Tool | Param | Default |
|------|-------|---------|
| `fmp_screener` | `market_cap_more_than` | 1 billion |
| `fmp_screener` | `volume_more_than` | 500 thousand |
| `fmp_screener` | `exchange` | NYSE,NASDAQ |
| `fmp_screener` | `limit` | 50 |
| `fmp_ohlcv` | `limit` | 90 |
| `fmp_news` | `limit` | 10 |
| `alpaca_list_orders` | `limit` | 50 |
| `run_script` | `timeout` | 60 (after change) |

### Category B — Hard limits (increase + document as fixed)
These are not overridable by Claude but are increased to be more practical.

| Item | Old | New |
|------|-----|-----|
| All external API timeouts (FMP, Alpaca, Serper) | 10s | 30s |
| `run_script` default timeout | 30s | 60s |
| `run_script` memory kill | 256MB | 512MB |

The updated values are documented in strategy_doc so Claude can plan around them.

## Changes

### 1. `scheduler/tools/fmp.py`
- All 4 `requests.*` calls: `timeout=10` → `timeout=30`
- `fmp_screener` docstring: append `(changeable)` to `market_cap_more_than`, `volume_more_than`, `exchange`, `limit` descriptions
- `fmp_ohlcv` docstring: append `(changeable)` to `limit` description
- `fmp_news` docstring: append `(changeable)` to `limit` description

### 2. `scheduler/tools/alpaca.py`
- All 5 `requests.*` calls: `timeout=10` → `timeout=30`
- `alpaca_list_orders` docstring: append `(changeable)` to `limit` description

### 3. `scheduler/tools/serper.py`
- `timeout=10` → `timeout=30`
- `num` param docstring: append `(changeable)`

### 4. `scheduler/tools/pyexec.py`
- `timeout: int = 30` → `timeout: int = 60` in function signature
- `256 * 1024 * 1024` → `512 * 1024 * 1024` in `_set_resource_limits`
- `timeout` docstring: update default value reference to 60, add `(changeable)`

### 5. `scheduler/agent.py` — strategy_doc
Append a `## System Constraints` section to `INITIAL_STRATEGY_DOC`:

```
## System Constraints
All tool defaults are starting points — pass explicit values to override.
Hard limits (not overridable): API calls timeout at 30s; run_script kills at 60s/512MB;
strategy backtest window is 60 days; only one probationary strategy at a time.
```

## Out of Scope
- Changing strategy_gate.py backtest window (60 days stays)
- Changing one-probation-at-a-time guard
- Changing memory block size limits (5500/2000/1000/2000 chars)
- Changing EOD order fetch cap (50)
- Any logic changes — this is documentation + timeout increases only
