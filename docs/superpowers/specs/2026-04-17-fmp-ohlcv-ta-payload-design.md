# Design: fmp_ohlcv → Professional TA Payload

**Date:** 2026-04-17  
**Status:** Approved  
**Scope:** Replace `fmp_ohlcv` with a pre-calculated, information-dense technical analysis payload covering 1D and 1W timeframes, institutional concepts, and 20 WorldQuant Alpha101 signals.

---

## Problem

`fmp_ohlcv` currently returns raw OHLCV candles (~750 tokens for 90 days). The agent must do all analysis itself from raw price data — inefficient use of context and reasoning budget. Most of the token spend goes to data the agent cannot act on directly.

**Goal:** Same or 20% more tokens, dramatically more actionable signal. Agent receives pre-calculated analysis, not raw data.

---

## Architecture

### Files Changed

| File | Change |
|------|--------|
| `scheduler/tools/fmp.py` | Replace `fmp_ohlcv` in-place with enriched version |
| `scheduler/tools/_ta.py` | **New** — all TA math and IC detection, imported by `fmp.py` |
| `scheduler/agent.py` | Update `TOOL_SCHEMAS` description for `fmp_ohlcv` |

### Data Fetch Strategy

- Internally fetch **260 daily candles** from FMP (enough for EMA200, 230-day VWAP correlation, Alpha#32)
- **Weekly candles derived by resampling daily** — no second FMP API call
- `limit` parameter controls how many raw OHLCV candles are exposed in output (default: 5)
- Function signature unchanged: `fmp_ohlcv(ticker, limit=5, api_key=None) -> dict`

### Dependencies

- Add `ta-lib` (TA-Lib Python wrapper) to `requirements.txt`
- Add `numpy` to `requirements.txt` (used by `_ta.py`)
- TA-Lib handles all standard indicators; custom Python functions handle ICs and Alpha101

---

## Output Structure

Estimated **~590 tokens** vs ~750 for raw 90-day OHLCV. Flat JSON, max 2 levels of nesting.

```json
{
  "meta": {
    "symbol": "AAPL",
    "as_of": "2026-04-17",
    "price": 184.75
  },
  "ohlcv_1d": [ /* last 5 daily candles: {d, o, h, l, c, v} */ ],
  "ohlcv_1w": [ /* last 5 weekly candles: {d, o, h, l, c, v} */ ],
  "momentum_1d": { /* RSI, MACD, Stoch, MFI */ },
  "trend_1d":    { /* EMA triple, ADX/DI, VWAP */ },
  "trend_1w":    { /* EMA triple, ADX */ },
  "volatility_1d": { /* ATR, Bollinger Bands */ },
  "volume_1d":   { /* vol_ratio, OBV */ },
  "price_structure": { /* S/R levels, pivot points, 52-week range */ },
  "ics_1d": { /* Order Blocks, FVGs, Breaker Blocks, Liquidity, Market Structure */ },
  "ics_1w": { /* Order Blocks, Market Structure */ },
  "patterns_1d": [ /* candlestick patterns, last 5 candles */ ],
  "patterns_1w": [ /* candlestick patterns, last 3 candles */ ],
  "alpha101":    { /* 20 WorldQuant alpha scalars */ }
}
```

---

## Section-by-Section Spec

### 1. Raw OHLCV

```json
"ohlcv_1d": [{"d":"2026-04-17","o":182.50,"h":185.20,"l":181.10,"c":184.75,"v":48200000}]
"ohlcv_1w": [{"d":"2026-04-14","o":180.00,"h":186.50,"l":179.20,"c":184.75,"v":210000000}]
```

Last 5 candles per timeframe. Keys abbreviated to save tokens.

---

### 2. Momentum (1D only)

#### RSI — Three periods

Params: RSI(7), RSI(14), RSI(21)

Per period: `cur`, `7d_hi`, `7d_lo`, `7d_avg`, `14d_hi`, `14d_lo`, `14d_avg`, `30d_hi`, `30d_lo`, `30d_avg`, `90d_hi`, `90d_lo`, `90d_avg`

Rolling stats over the RSI value series (not price). Identifies whether current RSI is historically extreme for this stock.

**Token cost:** 3 × 13 = 39 values

#### MACD — Standard only

Params: MACD(12, 26, 9)

Values: `macd_line`, `signal_line`, `histogram`, `hist_7d_hi`, `hist_7d_lo`, `hist_7d_avg`, `hist_14d_hi`, `hist_14d_lo`, `hist_14d_avg`, `crossover` ("bull"/"bear"/"none"), `crossover_bars_ago`, `divergence` ("bull"/"bear"/"none")

MACD on 1W dropped — weekly MACD lags too much for swing trading.

**Token cost:** ~14 values

#### Stochastic — Two periods

Params: Stoch(5,3,3) and Stoch(14,3,3)

Per period: `k`, `d`, `crossover` ("bull"/"bear"/"none"), `zone` ("overbought"/"oversold"/"neutral")

No rolling hi/lo/avg — %K is already range-normalized (0–100), so historical context adds no value.

**Token cost:** 2 × 4 = 8 values

#### MFI

Params: MFI(14)

Values: `cur`, `14d_hi`, `14d_lo`, `divergence` ("bull"/"bear"/"none")

Replaces CMF (noisier, less standardized). Catches institutional accumulation/distribution that RSI misses.

**Token cost:** 4 values

---

### 3. Trend (1D and 1W)

#### Triple EMA — Sampled every 5 candles

**1D params:** EMA(21), EMA(55), EMA(89)  
**1W params:** EMA(21), EMA(55)

Values:
- `samples`: last 5 sample points (every 5th candle), each with `date`, `ema21`, `ema55`, `ema89`
- `alignment`: "bull" (21>55>89) / "bear" / "mixed"
- `price_vs_ema21_pct`: % distance current close from EMA21
- `price_vs_ema55_pct`: % distance current close from EMA55

Fibonacci-based periods (21/55/89) are widely watched by institutional desks. Sampling every 5 candles captures slope without raw data dump.

**Token cost:** 5 samples × 5 values + 4 scalars = ~29 values per timeframe

#### ADX + Directional Index

Params: ADX(14) on 1D and 1W

**1D values:** `adx`, `di_plus`, `di_minus`, `trend_strength` ("strong"/"trending"/"ranging"), `14d_hi`, `14d_lo`  
**1W values:** `adx`, `trend_strength`

Thresholds: strong = ADX>25, trending = 20–25, ranging = <20

**Token cost:** ~8 values (1D) + 2 values (1W)

#### VWAP (1D only)

Weekly-anchored VWAP (anchored to Monday's open of current week).

Values: `vwap`, `price_vs_vwap_pct`, `slope` ("up"/"down"/"flat")

**Token cost:** 3 values

---

### 4. Volatility (1D only)

#### ATR

Params: ATR(14)

Values: `atr`, `atr_pct` (ATR as % of close), `14d_avg`, `30d_avg`, `atr_regime` ("expanding"/"contracting"/"stable")

`atr_regime`: expanding if current > 14d_avg × 1.1, contracting if < 0.9×, else stable.

**Token cost:** 5 values

#### Bollinger Bands

Params: BB(20, 2.0) and BB(20, 1.0) — dual standard deviation levels

Values: `upper_2sd`, `mid`, `lower_2sd`, `upper_1sd`, `lower_1sd`, `pct_b`, `bandwidth`, `14d_bw_hi`, `14d_bw_lo`, `squeeze` (bool — bandwidth in bottom 20% of 14d range)

Keltner Channels dropped — BB bandwidth alone captures the squeeze signal adequately.

**Token cost:** 10 values

---

### 5. Volume (1D only)

#### Volume Ratio

Values: `vol_ratio_1d` (current / 20d SMA), `vol_ratio_1w` (current week / 20w SMA), `10d_hi_ratio`, `10d_lo_ratio`

**Token cost:** 4 values

#### OBV Trend

OBV with 21-period EMA of OBV. Raw OBV values meaningless across symbols — return slope/divergence only.

Values: `slope` ("up"/"down"/"flat"), `vs_price` ("confirming"/"diverging"), `trend_days` (consecutive days above/below EMA)

**Token cost:** 3 values

---

### 6. Price Structure

#### Support / Resistance

**Detection algorithm:**
1. Swing highs/lows using 5-bar lookback
2. Cluster levels within 0.5% of each other
3. Score: (touch_count × 1.0) + (recency_weight × 0.5) + (volume_at_level × 0.3)
4. Return top 3 support + 3 resistance (1D), top 2 + 2 (1W)

Per level: `price`, `strength` ("strong"/"moderate"/"weak"), `last_tested` (date), `type`

**Token cost:** ~5 × 10 levels = 50 values

#### Daily Pivot Points (1D only)

Standard pivot from previous day H/L/C.

Values: `pp`, `r1`, `r2`, `s1`, `s2`

**Token cost:** 5 values

#### 52-Week Range (1D only)

Values: `wk52_hi`, `wk52_lo`, `wk52_pct` (where price sits, 0–100), `dist_from_hi_pct`, `dist_from_lo_pct`

Price >85 percentile = breakout candidate. Price <15 percentile = reversal zone.

**Token cost:** 5 values

---

### 7. Institutional Concepts (ICs)

#### Order Blocks — Both timeframes

**Definition:** Last bearish candle before a bullish impulse ≥ 2×ATR (bullish OB), or last bullish candle before bearish impulse ≥ 2×ATR (bearish OB).

Per OB: `type`, `date`, `ob_high`, `ob_low`, `ob_mid`, `tested` (bool), `broken` (bool), `stale` (bool — older than 60 trading days)

Max: **3 per 1D, 2 per 1W** — unbroken only, most recent first.

**Token cost:** 7 × 5 = 35 values

#### Fair Value Gaps (FVGs) — 1D only

**Definition:** 3-candle pattern where candle[i+1] does not overlap candle[i-1]. Gap must be ≥0.3% of close.

Per FVG: `type`, `date`, `gap_high`, `gap_low`, `gap_mid`, `filled` (bool), `fill_pct`

Max: **3** — unfilled or partially filled, most recent first.

**Token cost:** 7 × 3 = 21 values

#### Liquidity Levels — 1D only

**Definition:** 2+ swing highs/lows within 0.2% of each other = liquidity pool (stop clusters).

Per level: `type` ("buy_side"/"sell_side"), `price`, `touches`, `swept` (bool)

Max: **4 levels** (2 buy-side, 2 sell-side), unswept only, sorted by proximity to current price.

**Token cost:** 4 × 4 = 16 values

#### Market Structure — Both timeframes

Tracks HH/HL (uptrend) vs LH/LL (downtrend). Detects Market Structure Break (MSB).

Values: `structure` ("uptrend"/"downtrend"/"ranging"), `last_hh` ({date, price}), `last_hl` ({date, price}), `msb` (null or {direction, date, level})

**Token cost:** ~8 values × 2 timeframes = 16 values

#### Breaker Blocks — 1D only

Broken Order Blocks that flip polarity. Reuse OB detection; filter `broken == True`; flip type label to "breaker_bull"/"breaker_bear".

Max: **2**, most recent.

**Token cost:** ~14 values

---

### 8. Candlestick Patterns

Detected via TA-Lib. Return only patterns from last 5 candles (1D) and last 3 candles (1W).

**Patterns tracked:** Engulfing (bull/bear), Hammer, Inverted Hammer, Shooting Star, Doji (standard/dragonfly/gravestone), Morning Star, Evening Star, Marubozu (bull/bear), Inside Bar, Pin Bar

Per pattern: `pattern`, `date`, `signal` ("bull"/"bear"/"neutral")

Max 5 patterns per timeframe. Return `[]` if none — do not omit key.

**Token cost:** 0–15 values (bounded)

---

### 9. Alpha101 — 20 WorldQuant Signals

All adapted from cross-sectional to single-stock time-series percentile rank (60-day rolling window). All require only OHLCV + VWAP. Returned as flat dict of scalars.

Keys use descriptive names so the agent can interpret each signal without needing a legend or schema lookup.

| Output Key | Alpha | Signal Family | Lookback |
|------------|-------|---------------|----------|
| `a1_momentum_peak` | #1 Signed Vol-Adj Return | Price Momentum | 25d |
| `a2_vol_accel_corr` | #2 Return-Volume Correlation | Volume-Price | 68d |
| `a3_open_vol_ranked` | #3 Open-Volume Correlation (ranked) | Volume-Price | 70d |
| `a4_support_floor` | #4 Low Time-Series Rank | Mean Reversion | 9d |
| `a6_open_vol_raw` | #6 Raw Open-Volume Correlation | Volume-Price | 10d |
| `a7_vol_gated` | #7 ADV-Gated Momentum | Volume-Price | 67d |
| `a9_regime_5d` | #9 Conditional Delta (5d) | Mean Reversion | 6d |
| `a10_regime_4d` | #10 Conditional Delta (4d) | Mean Reversion | 5d |
| `a12_capitulation` | #12 Volume-Signed Price Change | Microstructure | 2d |
| `a20_gap_structure` | #20 Open vs Prior Extremes | Volatility | 2d |
| `a27_vwap_participation` | #27 Vol-VWAP Correlation Rank | Volume-Price | 68d |
| `a31_mean_rev` | #31 Multi-Timeframe Mean Reversion | Mean Reversion | 32d |
| `a32_vwap_persist` | #32 Long-Range VWAP Persistence | Volatility | 235d |
| `a34_vol_squeeze` | #34 Short/Long Vol Ratio Squeeze | Volatility | 65d |
| `a39_low_vol_drop` | #39 Decay-Weighted Volume Delta | Microstructure | 29d |
| `a41_geo_mid_vwap` | #41 Geometric Mid vs VWAP | Volume-Price | 1d |
| `a49_accel` | #49 Velocity Acceleration | Price Momentum | 21d |
| `a50_distribution` | #50 High-Volume Distribution | Microstructure | 20d |
| `a55_range_vol_corr` | #55 Close-in-Range vs Volume | Mean Reversion | 78d |
| `a101_bar_quality` | #101 Bar Quality Ratio | Microstructure | 1d |

**Top 5 priority additions** (most orthogonal to existing suite):
1. `a101` — Bar quality ratio: only indicator capturing candlestick body conviction
2. `a12` — 1-day capitulation detector: OBV can't identify this
3. `a34` — Relative squeeze detector: fires earlier and more selectively than BB bandwidth
4. `a49` — Linear momentum acceleration: catches breakout acceleration MACD misses
5. `a7` — Volume-gated direction: only fires on above-average volume days

**Token cost:** 20 scalars = ~30 values

---

## Token Budget

| Section | Est. Values | Est. Tokens |
|---------|-------------|-------------|
| OHLCV 1D + 1W | ~60 | ~90 |
| RSI (3 periods) | 39 | ~55 |
| MACD | 14 | ~20 |
| Stochastic (2 periods) | 8 | ~12 |
| MFI | 4 | ~6 |
| Triple EMA (1D + 1W) | ~58 | ~80 |
| ADX/DI (1D + 1W) | 10 | ~15 |
| VWAP | 3 | ~5 |
| ATR | 5 | ~8 |
| Bollinger Bands | 10 | ~15 |
| Volume Ratio + OBV | 7 | ~10 |
| S/R Levels | 50 | ~70 |
| Pivot Points + 52W | 10 | ~15 |
| Order Blocks | 35 | ~50 |
| FVGs | 21 | ~30 |
| Liquidity Levels | 16 | ~22 |
| Market Structure | 16 | ~22 |
| Breaker Blocks | 14 | ~20 |
| Patterns | 0–15 | ~15 |
| Alpha101 | 20 | ~30 |
| **TOTAL** | **~410** | **~590** |

Raw 90-day OHLCV today: ~750 tokens. **New payload: ~590 tokens, ~10× more signal.**

---

## Implementation Notes

### `_ta.py` structure

```
scheduler/tools/_ta.py
├── Utility functions
│   ├── ts_rank_pct(arr, window) -> np.ndarray
│   ├── decay_linear(arr, window) -> float
│   └── compute_returns(close) -> np.ndarray
├── Indicator calculators (ta-lib wrappers)
│   ├── calc_rsi(close, periods=[7,14,21]) -> dict
│   ├── calc_macd(close) -> dict
│   ├── calc_stoch(high, low, close) -> dict
│   ├── calc_mfi(high, low, close, volume) -> dict
│   ├── calc_ema_samples(close, periods, sample_every=5) -> dict
│   ├── calc_adx(high, low, close) -> dict
│   ├── calc_vwap(high, low, close, volume) -> dict
│   ├── calc_atr(high, low, close) -> dict
│   ├── calc_bollinger(close) -> dict
│   ├── calc_volume_ratio(volume) -> dict
│   ├── calc_obv(close, volume) -> dict
│   └── calc_patterns(open_, high, low, close) -> list
├── Price structure
│   ├── calc_support_resistance(high, low, close, volume) -> dict
│   ├── calc_pivot_points(high, low, close) -> dict
│   └── calc_52w_range(close) -> dict
├── Institutional Concepts
│   ├── detect_order_blocks(df, atr, max_count=3) -> list
│   ├── detect_fvg(df, min_gap_pct=0.003, max_count=3) -> list
│   ├── detect_liquidity_levels(df, max_count=4) -> list
│   ├── detect_market_structure(df) -> dict
│   └── detect_breaker_blocks(df, atr, max_count=2) -> list
└── Alpha101
    ├── alpha1(close, returns) -> float
    ├── alpha2(open_, close, volume) -> float
    ├── alpha3(open_, volume) -> float
    ├── alpha4(low) -> float
    ├── alpha6(open_, volume) -> float
    ├── alpha7(close, volume) -> float
    ├── alpha9(close) -> float
    ├── alpha10(close) -> float
    ├── alpha12(close, volume) -> float
    ├── alpha20(open_, high, low, close) -> float
    ├── alpha27(volume, vwap) -> float
    ├── alpha31(close, low, volume) -> float
    ├── alpha32(close, vwap) -> float
    ├── alpha34(close, returns) -> float
    ├── alpha39(close, volume) -> float
    ├── alpha41(high, low, vwap) -> float
    ├── alpha49(close) -> float
    ├── alpha50(high, volume) -> float
    ├── alpha55(close, high, low, volume) -> float
    └── alpha101(open_, high, low, close) -> float
```

### Critical constraints

- **NaN handling:** TA-Lib returns NaN for warmup periods. Always serialize as `null` in JSON output, never `0` — zero RSI is a valid extreme value.
- **OB/FVG staleness:** Any OB or FVG older than 60 trading days gets `"stale": true` — agent should discount these.
- **Weekly resampling:** Group daily candles by ISO week; weekly candle = first open, max high, min low, last close, sum volume of the week. Include the current (potentially incomplete) week — clearly labeled with the Monday date. Indicators calculated on weekly data use only complete weeks to avoid lookback contamination.
- **Swing high/low consistency:** Use the same 5-bar lookback for S/R, liquidity levels, and market structure. Inconsistent lookbacks create contradictory signals.
- **Tool self-containment:** `fmp.py` imports `_ta` at module level (not inside the function body) since we're on the Anthropic SDK direct path — Letta self-containment constraint no longer applies.
- **Tool schema update:** `agent.py` TOOL_SCHEMAS entry for `fmp_ohlcv` must be updated to describe the new output structure so Claude knows what fields to expect.

### Error handling

- If FMP returns fewer than 200 candles: calculate what indicators are possible, set `null` for those requiring more history
- If ta-lib import fails: raise `ImportError` with clear message (don't silently return raw data)
- Wrap entire enrichment in try/except — on any calculation error, return `{"error": str(e), "raw_ohlcv": last_5_candles}` so agent still has price context

---

## What Was Cut and Why

| Dropped | Reason |
|---------|--------|
| Keltner Channels | Redundant with BB squeeze signal |
| CMF (Chaikin Money Flow) | MFI is more sensitive and widely used; both add same signal |
| ROC (Rate of Change) | MACD histogram covers this |
| Supertrend | ADX + EMA alignment is superior confluence with fewer tokens |
| Weekly MACD | Lags too much for swing trading; weekly trend covered by EMA/ADX |
| Raw OBV value | Meaningless across symbols; slope/divergence is what matters |
