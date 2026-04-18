# Unified Screener + PEAD Integration Design

**Date:** 2026-04-17
**Status:** Approved

---

## Overview

Replace the current multi-preset screener architecture (4 separate functions) with a single `fmp_screener` function that has sensible defaults for the two universal constraints (market cap, volume), optional LLM-controlled filters for everything else, and a built-in PEAD toggle that is on by default.

This enables two things simultaneously:
1. The LLM reasons about screener parameters directly rather than picking from preset labels
2. Every pre_market run automatically surfaces post-earnings drift candidates alongside standard screener results — creating a natural experiment to prove PEAD as a screener

---

## What Changes

### Removed
- `fmp_screen_momentum`
- `fmp_screen_earnings_catalyst`
- `fmp_screen_quality_defensive`
- `fmp_screen_short_candidates`

All four removed from `fmp.py`, `registry.py`, and tests.

### Modified
- `fmp_screener` — expanded with full optional filter set + PEAD sub-flow
- `STATIC_PROMPT` — regime→screener table rewritten; PEAD candidate evaluation section added
- `test_fmp.py` — preset tests replaced with unified screener tests
- `test_tool_defaults.py`, `test_agent.py` — updated for new tool list

---

## Function Signature

```python
def fmp_screener(
    # Defaults always applied
    market_cap_more_than: int = 2_000_000_000,
    volume_more_than: int = 1_000_000,
    # Optional — LLM sets when it has a reason to
    market_cap_less_than: Optional[int] = None,
    volume_less_than: Optional[int] = None,
    price_more_than: Optional[float] = None,
    price_less_than: Optional[float] = None,
    beta_more_than: Optional[float] = None,
    beta_less_than: Optional[float] = None,
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    dividend_more_than: Optional[float] = None,
    dividend_less_than: Optional[float] = None,
    limit: int = 30,
    # PEAD
    pead: bool = True,
    pead_min_surprise_pct: float = 21.9,
    pead_lookback_days: int = 5,
    api_key: Optional[str] = None,
) -> list:
```

Valid `sector` values: Technology | Healthcare | Consumer Cyclical | Consumer Defensive | Financial Services | Industrials | Energy | Basic Materials | Communication Services | Real Estate | Utilities

---

## PEAD Sub-flow

Executes after the main screener call when `pead=True`. Adds 2 API calls.

### Step 1 — Bulk Earnings Surprises
```
GET /stable/earnings-surprises-bulk?year=YYYY&apikey=KEY
```
Filter results to:
- `earnings_date` within last `pead_lookback_days` **trading days** (not calendar days — skip weekends/holidays using a simple weekday counter)
- `surprise_pct = (epsActual - epsEstimated) / abs(epsEstimated) * 100 >= pead_min_surprise_pct`
- `abs(epsEstimated) > 0.01` — avoids near-zero denominator noise
- Take top 20 by `surprise_pct` descending

**Year boundary:** If current month is January, also call with `year=YYYY-1` and union results before filtering.

### Step 2 — Quote Batch for Filtering
```
GET /stable/quote?symbol=T1,T2,...T20&apikey=KEY
```
For each qualifying ticker, retrieve `marketCap`, `avgVolume`, `sector`. Apply:
- `marketCap >= market_cap_more_than`
- `avgVolume >= volume_more_than`
- `sector == sector` (only if `sector` param is set)

### Merged Result Shape

PEAD candidates appended to the main screener list:
```python
{
    "symbol": "NVDA",
    "price": 892.0,
    "marketCap": 2_200_000_000_000,
    "volume": 3_400_000,
    "sector": "Technology",
    "pead_candidate": True,
    "eps_surprise_pct": 34.2,
    "eps_actual": 5.16,
    "eps_estimated": 3.84,
    "earnings_date": "2026-04-14"
}
```

Non-PEAD results include `"pead_candidate": False` and no earnings fields.

Duplicates: if a ticker appears in both the main screener and PEAD results, the PEAD version wins (keeps earnings fields, sets `pead_candidate=True`).

---

## STATIC_PROMPT Changes

### Screener Guidance (replaces 4-preset regime table)

```
fmp_screener(market_cap_more_than=2B, volume_more_than=1M, [...optional...], pead=True)
  Default call (no optional params) is valid — returns broad universe + PEAD candidates.

  Bull/momentum:   beta_more_than=1.0, beta_less_than=2.8, sector=<leading sector>
  Bear/defensive:  beta_less_than=1.0, market_cap_more_than=5_000_000_000
  Shorts universe: beta_more_than=1.5
  Earnings season: no extra params needed — PEAD toggle handles earnings candidates

  Leading sector check: call fmp_ta on XLK, XLY, XLF, XLV — pick top 2 by
  price_vs_ema55_pct + volume_ratio. Use their sector name in fmp_screener.
```

### PEAD Candidate Evaluation (new section)

```
PEAD candidates (pead_candidate=True in screener results):
  Context: stock reported earnings N days ago with eps_surprise_pct above threshold.
           The drift window is ~10 trading days from earnings_date.

  Do NOT enter on earnings_date itself — gap day is too volatile.
  Entry criteria (via fmp_ta):
    - Price consolidating above the earnings gap (not fading back into the gap)
    - Volume declining from gap day (coiling)
    - ADX > 20 (trend has started)
    - Price above EMA21

  setup_type: use "pead" in trade_open — enables separate performance tracking.
  context_json: always include eps_surprise_pct and earnings_date fields.
  Exit discipline: close by earnings_date + 10 trading days OR stop hit.
                   Do not hold open-ended — the drift window expires.

  Skip PEAD candidates if: VIX > 80th percentile, earnings_date > 8 trading days ago,
  initial gap was >15% (drift largely captured), or revenue miss confirmed.
```

---

## Tests

### New tests in `test_fmp.py`
- `test_fmp_screener_pead_off` — `pead=False` makes exactly 1 API call (screener only)
- `test_fmp_screener_pead_on_merges` — mock screener + bulk earnings → results contain both regular (`pead_candidate=False`) and PEAD items (`pead_candidate=True`) with correct fields
- `test_fmp_screener_pead_year_boundary` — January call triggers two bulk earnings calls (current + prior year)
- `test_fmp_screener_pead_dedup` — ticker in both screener and PEAD results → single entry with `pead_candidate=True`

### Removed tests
All tests for `fmp_screen_momentum`, `fmp_screen_earnings_catalyst`, `fmp_screen_quality_defensive`, `fmp_screen_short_candidates`.

### Updated tests
- `test_tool_defaults.py` — reflects new tool list (no presets)
- `test_agent.py` — schema coverage updated for `fmp_screener` new params

---

## PEAD Threshold Provenance

The `pead_min_surprise_pct=21.9` default comes from the developer's own backtested research (R044 config, OOS 2021–2025):
- CAGR: +32.17%, Win rate: 53.2%, Max drawdown: -19.7%, 5/5 positive years
- Threshold = Q85 of EPS surprise distribution from 2015–2020 training data
- Aligns with CANSLIM's 25% floor and academic PEAD literature (15–30% sweet spot)

The live system uses this as a screener input, not a mechanical trading rule. The AI applies TA gating before any entry.

---

## Out of Scope

The following from the broader research are deferred to a future phase:
- `fmp_fundamentals` (key metrics TTM)
- `fmp_analyst_context` (estimates, grades, price targets)
- `fmp_insider_activity`
- `fmp_sector_snapshot`
- `fmp_macro_context`
