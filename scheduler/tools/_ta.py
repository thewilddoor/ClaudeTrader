"""Technical analysis helpers for fmp_ohlcv enrichment.

All public functions accept numpy float64 arrays (oldest-first) and return
plain Python dicts/lists safe for JSON serialisation. NaN from TA-Lib warmup
periods serialises as None, never 0 (zero RSI is a valid extreme value).
"""
import math
from collections import defaultdict
from datetime import date as date_type, timedelta
from typing import Optional

import numpy as np
try:
    import talib
except ImportError as _e:  # pragma: no cover
    raise ImportError(
        "TA-Lib C library not installed. See Dockerfile for build instructions."
    ) from _e


# ─── Utilities ───────────────────────────────────────────────────────────────

def _nan_to_none(val) -> Optional[float]:
    """Convert NaN/inf to None; round finite floats to 4 dp."""
    if val is None:
        return None
    try:
        if math.isnan(val) or math.isinf(val):
            return None
        return round(float(val), 4)
    except (TypeError, ValueError):
        return None


def _rolling_hi_lo_avg(arr: np.ndarray, window: int):
    """(hi, lo, avg) of last `window` non-NaN values, or (None, None, None)."""
    valid = arr[~np.isnan(arr)]
    if len(valid) < window:
        return None, None, None
    recent = valid[-window:]
    return (
        round(float(np.max(recent)), 4),
        round(float(np.min(recent)), 4),
        round(float(np.mean(recent)), 4),
    )


def ts_rank_pct(arr: np.ndarray, window: int) -> np.ndarray:
    """Rolling percentile rank. result[i] = fraction of window <= arr[i]."""
    result = np.full(len(arr), np.nan)
    for i in range(window - 1, len(arr)):
        w = arr[i - window + 1: i + 1]
        result[i] = float(np.sum(w <= w[-1])) / window
    return result


def decay_linear(arr: np.ndarray, window: int) -> float:
    """Linearly decay-weighted mean. Most recent element = highest weight."""
    if len(arr) < window:
        return float("nan")
    weights = np.arange(1, window + 1, dtype=float)
    weights /= weights.sum()
    return float(np.dot(weights, arr[-window:]))


def compute_returns(close: np.ndarray) -> np.ndarray:
    """Log returns ln(close[i]/close[i-1]). Length = len(close) - 1."""
    return np.diff(np.log(np.maximum(close, 1e-10)))


def resample_weekly(records: list) -> list:
    """Aggregate daily FMP records to weekly candles (oldest-first).

    Groups by ISO week (Monday as anchor). Incomplete current week is included
    and labelled with its Monday date. Indicators calculated on weekly data
    must use only complete weeks to avoid lookback contamination.

    Args:
        records: List of dicts with date/open/high/low/close/volume.
                 Must be sorted oldest-first.
    Returns:
        List of {d, o, h, l, c, v} weekly candle dicts, oldest-first.
    """
    weeks: dict = {}
    for r in records:
        d = date_type.fromisoformat(r["date"])
        monday = (d - timedelta(days=d.weekday())).isoformat()
        if monday not in weeks:
            weeks[monday] = []
        weeks[monday].append(r)

    result = []
    for monday_str in sorted(weeks):
        days = weeks[monday_str]
        result.append({
            "d": monday_str,
            "o": float(days[0]["open"]),
            "h": float(max(d["high"] for d in days)),
            "l": float(min(d["low"] for d in days)),
            "c": float(days[-1]["close"]),
            "v": float(sum(d["volume"] for d in days)),
        })
    return result


# ─── Momentum ────────────────────────────────────────────────────────────────

def calc_rsi(close: np.ndarray, periods: list = None) -> dict:
    """RSI for periods 7, 14, 21 with rolling hi/lo/avg stats."""
    if periods is None:
        periods = [7, 14, 21]
    result = {}
    for period in periods:
        rsi_vals = talib.RSI(close.astype("f8"), timeperiod=period)
        hi7, lo7, avg7 = _rolling_hi_lo_avg(rsi_vals, 7)
        hi14, lo14, avg14 = _rolling_hi_lo_avg(rsi_vals, 14)
        hi30, lo30, avg30 = _rolling_hi_lo_avg(rsi_vals, 30)
        hi90, lo90, avg90 = _rolling_hi_lo_avg(rsi_vals, 90)
        result[f"rsi_{period}"] = {
            "cur": _nan_to_none(rsi_vals[-1]),
            "7d_hi": hi7,   "7d_lo": lo7,   "7d_avg": avg7,
            "14d_hi": hi14, "14d_lo": lo14, "14d_avg": avg14,
            "30d_hi": hi30, "30d_lo": lo30, "30d_avg": avg30,
            "90d_hi": hi90, "90d_lo": lo90, "90d_avg": avg90,
        }
    return result


def calc_macd(close: np.ndarray) -> dict:
    """MACD(12,26,9) with histogram rolling stats, crossover detection, divergence."""
    c = close.astype("f8")
    macd_line, signal_line, histogram = talib.MACD(c, fastperiod=12, slowperiod=26, signalperiod=9)

    hi7, lo7, avg7 = _rolling_hi_lo_avg(histogram, 7)
    hi14, lo14, avg14 = _rolling_hi_lo_avg(histogram, 14)

    # Crossover: scan last 3 bars
    crossover, crossover_bars_ago = "none", None
    for lag in range(1, 4):
        if lag + 1 >= len(macd_line):
            break
        m_prev, m_curr = macd_line[-(lag + 1)], macd_line[-lag]
        s_prev, s_curr = signal_line[-(lag + 1)], signal_line[-lag]
        if any(np.isnan([m_prev, m_curr, s_prev, s_curr])):
            continue
        if m_prev < s_prev and m_curr >= s_curr:
            crossover, crossover_bars_ago = "bull", lag - 1
            break
        if m_prev > s_prev and m_curr <= s_curr:
            crossover, crossover_bars_ago = "bear", lag - 1
            break

    # Divergence: price trend vs histogram trend over 14 bars
    divergence = "none"
    valid_hist = histogram[~np.isnan(histogram)]
    if len(close) >= 14 and len(valid_hist) >= 14:
        price_trend = close[-1] - close[-14]
        hist_trend = valid_hist[-1] - valid_hist[-14]
        if price_trend > 0 and hist_trend < 0:
            divergence = "bear"
        elif price_trend < 0 and hist_trend > 0:
            divergence = "bull"

    return {
        "macd_line": _nan_to_none(macd_line[-1]),
        "signal_line": _nan_to_none(signal_line[-1]),
        "histogram": _nan_to_none(histogram[-1]),
        "hist_7d_hi": hi7,   "hist_7d_lo": lo7,   "hist_7d_avg": avg7,
        "hist_14d_hi": hi14, "hist_14d_lo": lo14, "hist_14d_avg": avg14,
        "crossover": crossover,
        "crossover_bars_ago": crossover_bars_ago,
        "divergence": divergence,
    }


def calc_stoch(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict:
    """Stochastic for (5,3,3) and (14,3,3) with zone and crossover."""
    result = {}
    for fastk, key in [(5, "stoch_5"), (14, "stoch_14")]:
        k, d = talib.STOCH(
            high.astype("f8"), low.astype("f8"), close.astype("f8"),
            fastk_period=fastk, slowk_period=3, slowk_matype=0,
            slowd_period=3, slowd_matype=0,
        )
        cur_k = _nan_to_none(k[-1])
        cur_d = _nan_to_none(d[-1])

        zone = "neutral"
        if cur_k is not None:
            if cur_k >= 80:
                zone = "overbought"
            elif cur_k <= 20:
                zone = "oversold"

        crossover = "none"
        if len(k) >= 2 and not any(np.isnan([k[-2], k[-1], d[-2], d[-1]])):
            if k[-2] < d[-2] and k[-1] >= d[-1]:
                crossover = "bull"
            elif k[-2] > d[-2] and k[-1] <= d[-1]:
                crossover = "bear"

        result[key] = {"k": cur_k, "d": cur_d, "crossover": crossover, "zone": zone}
    return result


def calc_mfi(high: np.ndarray, low: np.ndarray,
             close: np.ndarray, volume: np.ndarray) -> dict:
    """MFI(14) with 14d hi/lo and divergence vs price."""
    mfi = talib.MFI(high.astype("f8"), low.astype("f8"),
                    close.astype("f8"), volume.astype("f8"), timeperiod=14)
    hi14, lo14, _ = _rolling_hi_lo_avg(mfi, 14)

    divergence = "none"
    valid_mfi = mfi[~np.isnan(mfi)]
    if len(close) >= 14 and len(valid_mfi) >= 14:
        price_trend = close[-1] - close[-14]
        mfi_trend = valid_mfi[-1] - valid_mfi[-14]
        if price_trend < 0 and mfi_trend > 0:
            divergence = "bull"
        elif price_trend > 0 and mfi_trend < 0:
            divergence = "bear"

    return {"cur": _nan_to_none(mfi[-1]), "14d_hi": hi14, "14d_lo": lo14,
            "divergence": divergence}


# ─── Trend ───────────────────────────────────────────────────────────────────

def calc_ema_samples(close: np.ndarray, dates: list,
                     periods: list = None, sample_every: int = 5) -> dict:
    """Triple EMA sampled every `sample_every` candles (5 sample points returned).

    Args:
        close: Price array, oldest-first.
        dates: Corresponding date strings.
        periods: EMA periods to compute (e.g. [21,55,89] for 1D, [21,55] for 1W).
        sample_every: Return every Nth value (default 5 to compress output).
    """
    if periods is None:
        periods = [21, 55, 89]
    c = close.astype("f8")
    emas = {}
    for p in periods:
        emas[p] = talib.EMA(c, timeperiod=p)

    # Sample every 5th candle from the end, 5 points total
    n = len(close)
    indices = list(range(n - 1, max(n - 1 - sample_every * 5, -1), -sample_every))[:5]
    indices.reverse()

    samples = []
    for idx in indices:
        point = {"date": dates[idx]}
        for p in periods:
            val = emas[p][idx]
            point[f"ema{p}"] = _nan_to_none(val)
        samples.append(point)

    # Alignment at current bar
    cur_vals = {p: emas[p][-1] for p in periods}
    sorted_periods = sorted(periods)
    alignment = "mixed"
    if all(not np.isnan(cur_vals[p]) for p in sorted_periods):
        vals = [cur_vals[p] for p in sorted_periods]
        if all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)):
            alignment = "bull"
        elif all(vals[i] < vals[i + 1] for i in range(len(vals) - 1)):
            alignment = "bear"

    price_vs = {}
    for p in periods[:2]:  # report vs first two EMAs (21 and 55)
        ev = emas[p][-1]
        if not np.isnan(ev) and ev != 0:
            price_vs[f"price_vs_ema{p}_pct"] = round((close[-1] - ev) / ev * 100, 4)
        else:
            price_vs[f"price_vs_ema{p}_pct"] = None

    return {"ema_samples": samples, "alignment": alignment, **price_vs}


def calc_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray,
             timeframe: str = "1d") -> dict:
    """ADX(14) with trend_strength classification.

    1D returns: adx, di_plus, di_minus, trend_strength, 14d_hi, 14d_lo
    1W returns: adx, trend_strength (no DI to save tokens)
    """
    h, l, c = high.astype("f8"), low.astype("f8"), close.astype("f8")
    adx_vals = talib.ADX(h, l, c, timeperiod=14)
    cur_adx = _nan_to_none(adx_vals[-1])

    trend_strength = "ranging"
    if cur_adx is not None:
        if cur_adx > 25:
            trend_strength = "strong"
        elif cur_adx >= 20:
            trend_strength = "trending"

    hi14, lo14, _ = _rolling_hi_lo_avg(adx_vals, 14)

    if timeframe == "1w":
        return {"adx": cur_adx, "trend_strength": trend_strength}

    plus_di = talib.PLUS_DI(h, l, c, timeperiod=14)
    minus_di = talib.MINUS_DI(h, l, c, timeperiod=14)
    return {
        "adx": cur_adx,
        "di_plus": _nan_to_none(plus_di[-1]),
        "di_minus": _nan_to_none(minus_di[-1]),
        "trend_strength": trend_strength,
        "14d_hi": hi14,
        "14d_lo": lo14,
    }


def calc_vwap(high: np.ndarray, low: np.ndarray, close: np.ndarray,
              volume: np.ndarray, dates: list) -> dict:
    """Weekly-anchored VWAP (anchored to Monday open of current week).

    Also returns vwap_series (full array) for use by Alpha27/32/41.
    """
    # Compute VWAP anchored to Monday of the current week
    from datetime import date as dt
    today = dt.fromisoformat(dates[-1])
    monday = today - timedelta(days=today.weekday())
    monday_str = monday.isoformat()

    # Find anchor index
    anchor_idx = 0
    for i, d in enumerate(dates):
        if d >= monday_str:
            anchor_idx = i
            break

    typical = (high + low + close) / 3.0
    cum_tp_vol = np.cumsum(typical * volume)
    cum_vol = np.cumsum(volume)

    # Anchored VWAP: reset cumulation at anchor
    anchor_tp_vol = cum_tp_vol[anchor_idx - 1] if anchor_idx > 0 else 0.0
    anchor_vol = cum_vol[anchor_idx - 1] if anchor_idx > 0 else 0.0
    anchored_cum_tp_vol = cum_tp_vol - anchor_tp_vol
    anchored_cum_vol = cum_vol - anchor_vol

    vwap_series = np.where(
        anchored_cum_vol > 0,
        anchored_cum_tp_vol / anchored_cum_vol,
        typical,
    )

    cur_vwap = float(vwap_series[-1])
    cur_close = float(close[-1])
    price_vs_vwap_pct = round((cur_close - cur_vwap) / cur_vwap * 100, 4) if cur_vwap != 0 else None

    # Slope: compare vwap today vs 5 days ago
    slope = "flat"
    if len(vwap_series) >= 6:
        delta = vwap_series[-1] - vwap_series[-6]
        threshold = cur_vwap * 0.001  # 0.1% threshold
        if delta > threshold:
            slope = "up"
        elif delta < -threshold:
            slope = "down"

    return {
        "vwap": round(cur_vwap, 4),
        "price_vs_vwap_pct": price_vs_vwap_pct,
        "slope": slope,
        "vwap_series": vwap_series,  # full array for alpha functions
    }


# ─── Volatility ──────────────────────────────────────────────────────────────

def calc_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict:
    """ATR(14) with atr_pct, rolling averages, and regime classification."""
    atr_vals = talib.ATR(high.astype("f8"), low.astype("f8"), close.astype("f8"), timeperiod=14)
    cur_atr = float(atr_vals[-1]) if not np.isnan(atr_vals[-1]) else None
    _, _, avg14 = _rolling_hi_lo_avg(atr_vals, 14)
    _, _, avg30 = _rolling_hi_lo_avg(atr_vals, 30)

    atr_pct = None
    if cur_atr is not None and close[-1] != 0:
        atr_pct = round(cur_atr / close[-1] * 100, 4)

    atr_regime = "stable"
    if cur_atr is not None and avg14 is not None:
        if cur_atr > avg14 * 1.1:
            atr_regime = "expanding"
        elif cur_atr < avg14 * 0.9:
            atr_regime = "contracting"

    return {
        "atr": _nan_to_none(atr_vals[-1]),
        "atr_pct": atr_pct,
        "14d_avg": avg14,
        "30d_avg": avg30,
        "atr_regime": atr_regime,
    }


def calc_bollinger(close: np.ndarray, period: int = 20) -> dict:
    """Bollinger Bands at 1SD and 2SD with %B, bandwidth, and squeeze detection."""
    c = close.astype("f8")
    upper2, mid, lower2 = talib.BBANDS(c, timeperiod=period, nbdevup=2.0, nbdevdn=2.0)
    upper1, _,  lower1  = talib.BBANDS(c, timeperiod=period, nbdevup=1.0, nbdevdn=1.0)

    cur_upper2 = _nan_to_none(upper2[-1])
    cur_mid    = _nan_to_none(mid[-1])
    cur_lower2 = _nan_to_none(lower2[-1])
    cur_upper1 = _nan_to_none(upper1[-1])
    cur_lower1 = _nan_to_none(lower1[-1])

    pct_b = None
    bandwidth = None
    if all(v is not None for v in [cur_upper2, cur_lower2, cur_mid]) and cur_mid != 0:
        band_range = cur_upper2 - cur_lower2
        pct_b = round((close[-1] - cur_lower2) / band_range, 4) if band_range != 0 else None
        bandwidth = round(band_range / cur_mid * 100, 4)

    # Squeeze: is current bandwidth in bottom 20% of 14-day bandwidth range?
    squeeze = False
    hi_bw, lo_bw = None, None
    if cur_mid is not None:
        bw_series = np.where(mid != 0, (upper2 - lower2) / mid * 100, np.nan)
        hi_bw, lo_bw, _ = _rolling_hi_lo_avg(bw_series, 14)
        if hi_bw is not None and lo_bw is not None and bandwidth is not None:
            threshold = lo_bw + (hi_bw - lo_bw) * 0.20
            squeeze = bandwidth <= threshold

    return {
        "upper_2sd": cur_upper2,
        "mid":       cur_mid,
        "lower_2sd": cur_lower2,
        "upper_1sd": cur_upper1,
        "lower_1sd": cur_lower1,
        "pct_b":     pct_b,
        "bandwidth": bandwidth,
        "14d_bw_hi": hi_bw,
        "14d_bw_lo": lo_bw,
        "squeeze":   squeeze,
    }


# ─── Volume ──────────────────────────────────────────────────────────────────

def calc_volume_ratio(volume: np.ndarray, w_volume: np.ndarray) -> dict:
    """Volume ratio vs 20-day SMA (1D) and 20-week SMA (1W)."""
    vol_sma20 = float(np.mean(volume[-20:])) if len(volume) >= 20 else float(np.mean(volume))
    cur_vol_ratio = round(volume[-1] / vol_sma20, 4) if vol_sma20 > 0 else None

    # 10-day high/low of vol_ratio for context
    ratios_10d = volume[-10:] / vol_sma20 if vol_sma20 > 0 else np.ones(10)
    hi_ratio = round(float(np.max(ratios_10d)), 4)
    lo_ratio = round(float(np.min(ratios_10d)), 4)

    # Weekly ratio
    w_sma20 = float(np.mean(w_volume[-20:])) if len(w_volume) >= 20 else float(np.mean(w_volume))
    cur_w_ratio = round(w_volume[-1] / w_sma20, 4) if w_sma20 > 0 else None

    return {
        "vol_ratio_1d": cur_vol_ratio,
        "vol_ratio_1w": cur_w_ratio,
        "10d_hi_ratio": hi_ratio,
        "10d_lo_ratio": lo_ratio,
    }


def calc_obv(close: np.ndarray, volume: np.ndarray) -> dict:
    """OBV with 21-EMA slope, price divergence, and consecutive trend days."""
    obv = talib.OBV(close.astype("f8"), volume.astype("f8"))
    obv_ema21 = talib.EMA(obv, timeperiod=21)

    # Slope: OBV above/below its 21-EMA for last 5 bars
    slope = "flat"
    if not np.isnan(obv_ema21[-1]):
        above = obv[-5:] > obv_ema21[-5:]
        frac_above = np.sum(above) / 5
        if frac_above >= 0.8:
            slope = "up"
        elif frac_above <= 0.2:
            slope = "down"

    # Divergence: OBV slope vs price slope over 14 bars
    vs_price = "confirming"
    if len(close) >= 14 and not np.isnan(obv[-14]):
        price_dir = 1 if close[-1] > close[-14] else -1
        obv_dir = 1 if obv[-1] > obv[-14] else -1
        if price_dir != obv_dir:
            vs_price = "diverging"

    # Consecutive trend days: how many bars OBV has been above/below its EMA
    trend_days = 0
    if not np.isnan(obv_ema21[-1]):
        above_now = obv[-1] > obv_ema21[-1]
        for i in range(1, len(obv)):
            if np.isnan(obv_ema21[-i]):
                break
            if (obv[-i] > obv_ema21[-i]) == above_now:
                trend_days += 1
            else:
                break

    return {"slope": slope, "vs_price": vs_price, "trend_days": trend_days}


# ─── Price Structure ──────────────────────────────────────────────────────────

def _find_swing_highs_lows(high: np.ndarray, low: np.ndarray,
                            lookback: int = 5) -> tuple:
    """Return (swing_high_indices, swing_low_indices) using `lookback`-bar window.

    A bar is a swing high if its high is the maximum of the surrounding window.
    Same 5-bar lookback used by S/R, liquidity levels, and market structure
    to ensure consistency across all IC calculations.
    """
    n = len(high)
    sh_idx, sl_idx = [], []
    for i in range(lookback, n - lookback):
        window_h = high[i - lookback: i + lookback + 1]
        window_l = low[i - lookback: i + lookback + 1]
        if high[i] == np.max(window_h):
            sh_idx.append(i)
        if low[i] == np.min(window_l):
            sl_idx.append(i)
    return sh_idx, sl_idx


def calc_support_resistance(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                             volume: np.ndarray, dates: list,
                             n_support: int = 3, n_resist: int = 3,
                             cluster_pct: float = 0.005) -> list:
    """Swing-based support/resistance levels with touch count and recency scoring.

    Detection:
    1. Find swing highs/lows (5-bar lookback, consistent with ICs).
    2. Cluster levels within `cluster_pct` (0.5%) of each other.
    3. Score = touch_count + recency_weight (more recent = higher).
    4. Return top `n_support` below current price, top `n_resist` above.
    """
    sh_idx, sl_idx = _find_swing_highs_lows(high, low)
    cur_price = float(close[-1])
    n = len(close)

    def cluster(prices_indices, is_high: bool):
        levels = []
        raw_vals = [(high[i] if is_high else low[i], i) for i in prices_indices]
        for price, idx in raw_vals:
            matched = None
            for lv in levels:
                if abs(lv["price"] - price) / max(lv["price"], 1e-10) < cluster_pct:
                    matched = lv
                    break
            if matched:
                matched["price"] = (matched["price"] * matched["touches"] + price) / (matched["touches"] + 1)
                matched["touches"] += 1
                matched["last_idx"] = max(matched["last_idx"], idx)
            else:
                levels.append({"price": price, "touches": 1, "last_idx": idx})
        return levels

    resist_levels = cluster(sh_idx, is_high=True)
    support_levels = cluster(sl_idx, is_high=False)

    def score(lv):
        recency = lv["last_idx"] / n  # 0–1, higher = more recent
        return lv["touches"] + recency * 0.5

    def strength(touches):
        if touches >= 3:
            return "strong"
        if touches == 2:
            return "moderate"
        return "weak"

    result = []
    above = sorted([lv for lv in resist_levels if lv["price"] > cur_price],
                   key=score, reverse=True)[:n_resist]
    below = sorted([lv for lv in support_levels if lv["price"] < cur_price],
                   key=score, reverse=True)[:n_support]

    for lv in above:
        result.append({"type": "resistance", "price": round(lv["price"], 4),
                        "strength": strength(lv["touches"]),
                        "last_tested": dates[lv["last_idx"]]})
    for lv in below:
        result.append({"type": "support", "price": round(lv["price"], 4),
                        "strength": strength(lv["touches"]),
                        "last_tested": dates[lv["last_idx"]]})
    return result


def calc_pivot_points(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> dict:
    """Standard pivot points from previous day's H/L/C."""
    ph, pl, pc = float(high[-2]), float(low[-2]), float(close[-2])
    pp = (ph + pl + pc) / 3
    r1 = 2 * pp - pl
    r2 = pp + (ph - pl)
    s1 = 2 * pp - ph
    s2 = pp - (ph - pl)
    return {
        "pp": round(pp, 4), "r1": round(r1, 4), "r2": round(r2, 4),
        "s1": round(s1, 4), "s2": round(s2, 4),
    }


def calc_52w_range(close: np.ndarray) -> dict:
    """52-week high/low and percentile position of current close."""
    days = min(252, len(close))
    period = close[-days:]
    hi = float(np.max(period))
    lo = float(np.min(period))
    cur = float(close[-1])
    pct = round((cur - lo) / (hi - lo) * 100, 2) if hi != lo else 50.0
    return {
        "wk52_hi": round(hi, 4),
        "wk52_lo": round(lo, 4),
        "wk52_pct": pct,
        "dist_from_hi_pct": round((hi - cur) / hi * 100, 4) if hi != 0 else None,
        "dist_from_lo_pct": round((cur - lo) / lo * 100, 4) if lo != 0 else None,
    }


# ─── Institutional Concepts ───────────────────────────────────────────────────

_STALE_DAYS = 60  # OBs/FVGs older than this are marked stale


def detect_order_blocks(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                         close: np.ndarray, dates: list, atr: float,
                         min_impulse_atr: float = 2.0, max_count: int = 3) -> list:
    """Detect unbroken Order Blocks.

    Bullish OB: bearish candle (close < open) immediately before a bullish
    impulse >= min_impulse_atr × ATR.
    Bearish OB: bullish candle immediately before a bearish impulse of same size.
    """
    n = len(close)
    obs = []
    for i in range(1, n - 1):
        impulse = abs(close[i + 1] - close[i])
        if impulse < min_impulse_atr * atr:
            continue
        is_bullish_ob = (close[i] < open_[i] and close[i + 1] > open_[i + 1])
        is_bearish_ob = (close[i] > open_[i] and close[i + 1] < open_[i + 1])
        if not (is_bullish_ob or is_bearish_ob):
            continue
        ob_type = "bullish" if is_bullish_ob else "bearish"
        ob_high = float(high[i])
        ob_low  = float(low[i])
        ob_mid  = round((ob_high + ob_low) / 2, 4)

        # tested: any subsequent candle entered the OB zone
        if is_bullish_ob:
            tested = any(low[j] <= ob_high and high[j] >= ob_low for j in range(i + 2, n))
            broken = any(close[j] < ob_low for j in range(i + 2, n))
        else:
            tested = any(low[j] <= ob_high and high[j] >= ob_low for j in range(i + 2, n))
            broken = any(close[j] > ob_high for j in range(i + 2, n))

        if broken:
            continue  # skip broken OBs (they become breaker blocks)

        stale = (n - 1 - i) > _STALE_DAYS
        obs.append({
            "type": ob_type, "date": dates[i],
            "ob_high": round(ob_high, 4), "ob_low": round(ob_low, 4), "ob_mid": ob_mid,
            "tested": tested, "broken": False, "stale": stale,
        })

    # Most recent unbroken OBs first
    obs.reverse()
    return obs[:max_count]


def detect_fvg(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               dates: list, min_gap_pct: float = 0.003, max_count: int = 3) -> list:
    """Detect Fair Value Gaps (3-candle imbalance, gap >= min_gap_pct of close)."""
    n = len(close)
    fvgs = []
    for i in range(1, n - 1):
        # Bullish FVG: high[i-1] < low[i+1]
        gap_lo = float(high[i - 1])
        gap_hi = float(low[i + 1])
        if gap_hi > gap_lo and (gap_hi - gap_lo) / max(close[i], 1e-10) >= min_gap_pct:
            filled = any(low[j] <= gap_lo for j in range(i + 2, n))
            if not filled:
                fvgs.append({
                    "type": "bullish", "date": dates[i],
                    "gap_high": round(gap_hi, 4), "gap_low": round(gap_lo, 4),
                    "gap_mid": round((gap_hi + gap_lo) / 2, 4),
                    "filled": False, "fill_pct": 0.0,
                    "stale": (n - 1 - i) > _STALE_DAYS,
                })
            continue
        # Bearish FVG: low[i-1] > high[i+1]
        gap_hi = float(low[i - 1])
        gap_lo = float(high[i + 1])
        if gap_hi > gap_lo and (gap_hi - gap_lo) / max(close[i], 1e-10) >= min_gap_pct:
            filled = any(high[j] >= gap_hi for j in range(i + 2, n))
            if not filled:
                fvgs.append({
                    "type": "bearish", "date": dates[i],
                    "gap_high": round(gap_hi, 4), "gap_low": round(gap_lo, 4),
                    "gap_mid": round((gap_hi + gap_lo) / 2, 4),
                    "filled": False, "fill_pct": 0.0,
                    "stale": (n - 1 - i) > _STALE_DAYS,
                })

    fvgs.reverse()
    return fvgs[:max_count]


def detect_liquidity_levels(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                              dates: list, tolerance_pct: float = 0.002,
                              min_touches: int = 2, max_count: int = 4) -> list:
    """Equal highs/lows within tolerance = stop-order cluster (liquidity pool)."""
    sh_idx, sl_idx = _find_swing_highs_lows(high, low)
    cur_price = float(close[-1])

    def cluster_levels(indices, arr, ltype):
        pools = []
        for idx in indices:
            price = float(arr[idx])
            matched = next((p for p in pools
                            if abs(p["price"] - price) / max(p["price"], 1e-10) < tolerance_pct
                            and p["type"] == ltype), None)
            if matched:
                matched["price"] = (matched["price"] * matched["touches"] + price) / (matched["touches"] + 1)
                matched["touches"] += 1
                matched["last_idx"] = max(matched["last_idx"], idx)
            else:
                pools.append({"type": ltype, "price": price, "touches": 1, "last_idx": idx})
        return [p for p in pools if p["touches"] >= min_touches]

    buy_side  = cluster_levels(sh_idx, high, "buy_side")
    sell_side = cluster_levels(sl_idx, low,  "sell_side")

    all_levels = buy_side + sell_side
    all_levels.sort(key=lambda x: abs(x["price"] - cur_price))

    result = []
    buy_count = sell_count = 0
    for lv in all_levels:
        if lv["type"] == "buy_side" and buy_count >= max_count // 2:
            continue
        if lv["type"] == "sell_side" and sell_count >= max_count // 2:
            continue
        result.append({
            "type": lv["type"],
            "price": round(lv["price"], 4),
            "touches": lv["touches"],
            "swept": False,
        })
        if lv["type"] == "buy_side":
            buy_count += 1
        else:
            sell_count += 1
        if len(result) >= max_count:
            break
    return result


def detect_market_structure(high: np.ndarray, low: np.ndarray,
                              close: np.ndarray, dates: list) -> dict:
    """Track HH/HL (uptrend) vs LH/LL (downtrend) and detect MSB."""
    sh_idx, sl_idx = _find_swing_highs_lows(high, low)

    last_highs = [(dates[i], round(float(high[i]), 4)) for i in sh_idx[-3:]]
    last_lows  = [(dates[i], round(float(low[i]),  4)) for i in sl_idx[-3:]]

    structure = "ranging"
    if len(last_highs) >= 2 and len(last_lows) >= 2:
        hh = last_highs[-1][1] > last_highs[-2][1]
        hl = last_lows[-1][1]  > last_lows[-2][1]
        lh = last_highs[-1][1] < last_highs[-2][1]
        ll = last_lows[-1][1]  < last_lows[-2][1]
        if hh and hl:
            structure = "uptrend"
        elif lh and ll:
            structure = "downtrend"

    last_hh = {"date": last_highs[-1][0], "price": last_highs[-1][1]} if last_highs else None
    last_hl = {"date": last_lows[-1][0],  "price": last_lows[-1][1]}  if last_lows  else None

    # MSB: close beyond last swing high (in downtrend) or last swing low (in uptrend)
    msb = None
    cur_close = float(close[-1])
    if structure == "downtrend" and last_hh and cur_close > last_hh["price"]:
        msb = {"direction": "bullish", "date": dates[-1], "level": last_hh["price"]}
    elif structure == "uptrend" and last_hl and cur_close < last_hl["price"]:
        msb = {"direction": "bearish", "date": dates[-1], "level": last_hl["price"]}

    return {"structure": structure, "last_hh": last_hh, "last_hl": last_hl, "msb": msb}


def detect_breaker_blocks(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                           close: np.ndarray, dates: list, atr: float,
                           max_count: int = 2) -> list:
    """Broken OBs that flip polarity — become high-probability reversal zones."""
    n = len(close)
    breakers = []
    for i in range(1, n - 1):
        impulse = abs(close[i + 1] - close[i])
        if impulse < 2.0 * atr:
            continue
        is_bullish_ob = (close[i] < open_[i] and close[i + 1] > open_[i + 1])
        is_bearish_ob = (close[i] > open_[i] and close[i + 1] < open_[i + 1])
        if not (is_bullish_ob or is_bearish_ob):
            continue
        ob_high = float(high[i])
        ob_low  = float(low[i])

        # Check if broken: price closed beyond the OB's far edge
        if is_bullish_ob:
            broken = any(close[j] < ob_low for j in range(i + 2, n))
            flip_type = "breaker_bear"
        else:
            broken = any(close[j] > ob_high for j in range(i + 2, n))
            flip_type = "breaker_bull"

        if not broken:
            continue

        stale = (n - 1 - i) > _STALE_DAYS
        breakers.append({
            "type": flip_type, "date": dates[i],
            "ob_high": round(ob_high, 4), "ob_low": round(ob_low, 4),
            "ob_mid": round((ob_high + ob_low) / 2, 4),
            "stale": stale,
        })

    breakers.reverse()
    return breakers[:max_count]


def calc_ics(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
             close: np.ndarray, volume: np.ndarray, dates: list,
             timeframe: str = "1d") -> dict:
    """Convenience wrapper — assembles all IC sections for one timeframe."""
    atr_vals = talib.ATR(high.astype("f8"), low.astype("f8"), close.astype("f8"), timeperiod=14)
    atr = float(atr_vals[-1]) if not np.isnan(atr_vals[-1]) else float(np.nanmean(atr_vals))

    ob_max = 3 if timeframe == "1d" else 2
    return {
        "order_blocks":     detect_order_blocks(open_, high, low, close, dates, atr, max_count=ob_max),
        "fvgs":             detect_fvg(high, low, close, dates),
        "liquidity_levels": detect_liquidity_levels(high, low, close, dates),
        "market_structure": detect_market_structure(high, low, close, dates),
        "breaker_blocks":   detect_breaker_blocks(open_, high, low, close, dates, atr),
    }


# ─── Candlestick Patterns ─────────────────────────────────────────────────────

_PATTERN_FUNCS = [
    # (talib_func, name, signal)
    (talib.CDLENGULFING,     "Engulfing",      None),    # ±100 = bull/bear
    (talib.CDLHAMMER,        "Hammer",         "bull"),
    (talib.CDLINVERTEDHAMMER,"InvHammer",      "bull"),
    (talib.CDLSHOOTINGSTAR,  "ShootingStar",   "bear"),
    (talib.CDLDOJI,          "Doji",           "neutral"),
    (talib.CDLDRAGONFLYDOJI, "DragonflyDoji",  "bull"),
    (talib.CDLGRAVESTONEDOJI,"GravestoneDoji", "bear"),
    (talib.CDLMORNINGSTAR,   "MorningStar",    "bull"),
    (talib.CDLEVENINGSTAR,   "EveningStar",    "bear"),
    (talib.CDLMARUBOZU,      "Marubozu",       None),    # ±100 = bull/bear
    (talib.CDLHARAMI,        "InsideBar",      None),    # ±100 = bull/bear
    (talib.CDLPIERCING,      "PinBar",         "bull"),
]


def calc_patterns(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                  close: np.ndarray, dates: list, lookback: int = 5,
                  max_patterns: int = 5) -> list:
    """Detect candlestick patterns in the last `lookback` candles.

    Returns list of {pattern, date, signal} dicts, most recent first, capped at max_patterns.
    Returns [] if no patterns found — never omits the key in caller.
    """
    o = open_.astype("f8")
    h = high.astype("f8")
    l = low.astype("f8")
    c = close.astype("f8")
    n = len(c)

    found = []
    for fn, name, fixed_signal in _PATTERN_FUNCS:
        try:
            result = fn(o, h, l, c)
        except Exception:
            continue
        # Check last `lookback` bars
        for i in range(max(0, n - lookback), n):
            val = int(result[i])
            if val == 0:
                continue
            if fixed_signal is not None:
                signal = fixed_signal
            else:
                signal = "bull" if val > 0 else "bear"
            found.append({"pattern": name, "date": dates[i], "signal": signal, "_idx": i})

    # Sort by recency (most recent first), deduplicate same bar+pattern
    seen = set()
    unique = []
    for item in sorted(found, key=lambda x: x["_idx"], reverse=True):
        key = (item["date"], item["pattern"])
        if key not in seen:
            seen.add(key)
            # Filter out neutral signals — only return bull/bear patterns
            if item["signal"] != "neutral":
                unique.append({"pattern": item["pattern"], "date": item["date"], "signal": item["signal"]})

    return unique[:max_patterns]


# ─── Alpha101 ────────────────────────────────────────────────────────────────
# Adapted from WorldQuant 101 Formulaic Alphas (Kakushadze 2016).
# Cross-sectional rank() adapted to single-stock 60-day rolling percentile rank.
# All functions return a scalar float (or None on insufficient data).

_RANK_WIN = 60  # rolling window for ts_rank_pct adaptations


def _safe(val) -> Optional[float]:
    """Round to 4dp, None on NaN/inf."""
    return _nan_to_none(val)


def _alpha1(close: np.ndarray, returns: np.ndarray) -> Optional[float]:
    """Recency of momentum peak. High=peak was recent (bull). Low=peak stale (bear)."""
    window, argmax_win = 20, 5
    if len(close) < window + argmax_win:
        return None
    series = np.empty(argmax_win)
    for k in range(argmax_win):
        idx = -(argmax_win - k)
        r = float(returns[idx]) if idx < -1 else float(returns[-1])
        c = float(close[idx])
        val = float(np.std(returns[idx - window: idx], ddof=1)) if r < 0 else c
        series[k] = np.sign(val) * (val ** 2)
    return float(np.argmax(series))


def _alpha2(open_: np.ndarray, close: np.ndarray, volume: np.ndarray) -> Optional[float]:
    """Volume acceleration vs intraday gain correlation. Pos=accumulation."""
    if len(close) < 8:
        return None
    log_vol = np.log(np.maximum(volume, 1.0))
    d2_log_vol = np.diff(np.diff(log_vol))
    intraday = (close - open_) / np.maximum(open_, 1e-10)
    win = 6
    dv = d2_log_vol[-win:]
    ir = intraday[-win - 2: -2] if len(intraday) >= win + 2 else intraday[:win]
    if len(dv) < win or len(ir) < win or np.std(dv) < 1e-10 or np.std(ir) < 1e-10:
        return None
    return _safe(-float(np.corrcoef(dv, ir)[0, 1]))


def _alpha3(open_: np.ndarray, volume: np.ndarray) -> Optional[float]:
    """Open rising on low volume = positive (squeeze). Neg = distribution."""
    win = 10
    if len(open_) < win:
        return None
    o, v = open_[-win:].astype(float), volume[-win:].astype(float)
    if np.std(o) < 1e-10 or np.std(v) < 1e-10:
        return 0.0
    return _safe(-float(np.corrcoef(o, v)[0, 1]))


def _alpha4(low: np.ndarray, window: int = 9) -> Optional[float]:
    """Rising floor = overbought floor → mean reversion expected."""
    if len(low) < window:
        return None
    sl = low[-window:]
    return _safe(-float(np.sum(sl <= sl[-1])) / window)


def _alpha6(open_: np.ndarray, volume: np.ndarray, window: int = 10) -> Optional[float]:
    """Raw open-volume correlation. Pos=thin-market rally."""
    if len(open_) < window:
        return None
    o, v = open_[-window:].astype(float), volume[-window:].astype(float)
    if np.std(o) < 1e-10 or np.std(v) < 1e-10:
        return 0.0
    return _safe(-float(np.corrcoef(o, v)[0, 1]))


def _alpha7(close: np.ndarray, volume: np.ndarray,
            adv_win: int = 20, delta_win: int = 7, rank_win: int = 60) -> Optional[float]:
    """Volume-gated directional momentum. Returns 1.0 (neutral) on low-volume days."""
    if len(close) < rank_win + delta_win:
        return None
    adv20 = float(np.mean(volume[-adv_win:]))
    if volume[-1] <= adv20:
        return 1.0
    delta7 = close[-1] - close[-1 - delta_win]
    deltas = np.array([abs(close[i] - close[i - delta_win])
                       for i in range(delta_win, len(close))])[-rank_win:]
    rank = float(np.sum(deltas <= abs(delta7))) / rank_win
    return _safe(-rank * float(np.sign(delta7)))


def _alpha9(close: np.ndarray, window: int = 5) -> Optional[float]:
    """Auto-switch: trend-follow in consistent trends, mean-revert in choppy markets."""
    if len(close) < window + 1:
        return None
    deltas = np.diff(close[-(window + 1):])
    today_delta = float(close[-1] - close[-2])
    if float(np.min(deltas)) > 0:
        return today_delta
    if float(np.max(deltas)) < 0:
        return today_delta
    return -today_delta


def _alpha10(close: np.ndarray) -> Optional[float]:
    """Same as alpha9 but 4-day window (catches shorter regime bursts)."""
    return _alpha9(close, window=4)


def _alpha12(close: np.ndarray, volume: np.ndarray) -> Optional[float]:
    """Capitulation detector: volume spike + price drop = positive (buy signal)."""
    if len(close) < 2:
        return None
    return _safe(float(np.sign(volume[-1] - volume[-2])) * -(close[-1] - close[-2]))


def _alpha20(open_: np.ndarray, high: np.ndarray,
             low: np.ndarray, close: np.ndarray) -> Optional[float]:
    """Gap structure: how today's open compares to yesterday's H/L/C."""
    if len(open_) < 2:
        return None
    avg = max(float(close[-2]), 1e-10)
    gf_high  = (open_[-1] - high[-2])  / avg
    gf_close = (open_[-1] - close[-2]) / avg
    gf_low   = (open_[-1] - low[-2])   / avg
    return _safe(-float(gf_high + gf_close + gf_low))


def _alpha27(volume: np.ndarray, vwap: np.ndarray,
             corr_win: int = 6, rank_win: int = 60) -> Optional[float]:
    """Vol-VWAP correlation rank. Dislocation = +1 (opportunity). Aligned = -1."""
    if len(volume) < rank_win + corr_win:
        return None
    corrs = []
    for i in range(len(volume) - corr_win + 1):
        v_sl = volume[i: i + corr_win].astype(float)
        w_sl = vwap[i: i + corr_win].astype(float)
        if np.std(v_sl) < 1e-10 or np.std(w_sl) < 1e-10:
            corrs.append(0.0)
        else:
            corrs.append(float(np.corrcoef(v_sl, w_sl)[0, 1]))
    avg_corr = float(np.mean(corrs[-2:]))
    median = float(np.median(corrs[-rank_win:]))
    return -1.0 if avg_corr > median else 1.0


def _alpha31(close: np.ndarray, low: np.ndarray, volume: np.ndarray,
             delta_long: int = 10, delta_short: int = 3,
             adv_win: int = 20, corr_win: int = 12) -> Optional[float]:
    """Multi-timeframe mean reversion with volume-at-low confirmation."""
    if len(close) < adv_win + corr_win + delta_long:
        return None
    delta10 = np.array([close[i] - close[i - delta_long]
                        for i in range(delta_long, len(close))])[-delta_long:]
    weights = np.arange(1, delta_long + 1, dtype=float)
    weights /= weights.sum()
    comp1 = 1.0 - float(np.sum(delta10 <= delta10[-1])) / len(delta10)
    comp2 = -float(np.sign(close[-1] - close[-1 - delta_short]))
    adv20 = np.array([np.mean(volume[max(0, i - adv_win): i])
                      for i in range(adv_win, len(volume))])
    l_sl  = low[-corr_win:].astype(float)
    a_sl  = adv20[-corr_win:].astype(float)
    if np.std(l_sl) < 1e-10 or np.std(a_sl) < 1e-10:
        comp3 = 0.0
    else:
        comp3 = float(np.sign(np.corrcoef(l_sl, a_sl)[0, 1]))
    return _safe(comp1 + comp2 + comp3)


def _alpha32(close: np.ndarray, vwap: np.ndarray,
             short_win: int = 7, long_win: int = 230, lag: int = 5) -> Optional[float]:
    """Long-range VWAP autocorrelation + short-term mean reversion."""
    if len(close) < long_win + lag:
        return None
    mean7 = float(np.mean(close[-short_win:]))
    std7  = float(np.std(close[-short_win:])) or 1.0
    comp1 = (mean7 - close[-1]) / std7
    vw_sl = vwap[-long_win:].astype(float)
    cl_sl = close[-long_win - lag: -lag].astype(float)
    if np.std(vw_sl) < 1e-10 or np.std(cl_sl) < 1e-10:
        comp2 = 0.0
    else:
        comp2 = float(np.corrcoef(vw_sl, cl_sl)[0, 1])
    return _safe(comp1 + 20.0 * comp2)


def _alpha34(close: np.ndarray, returns: np.ndarray,
             short_vol: int = 2, long_vol: int = 5, rank_win: int = 60) -> Optional[float]:
    """Short/long vol ratio squeeze. High = vol compressed (pre-breakout)."""
    if len(returns) < rank_win + long_vol:
        return None
    ratios = []
    for i in range(long_vol - 1, len(returns)):
        s = float(np.std(returns[i - short_vol + 1: i + 1], ddof=1)) if short_vol > 1 else abs(returns[i])
        l = float(np.std(returns[i - long_vol + 1: i + 1], ddof=1)) or 1e-10
        ratios.append(s / l)
    if len(ratios) < rank_win:
        return None
    recent = np.array(ratios[-rank_win:])
    vol_rank = float(np.sum(recent <= recent[-1])) / rank_win
    dabs = np.abs(np.diff(close))[-rank_win:]
    d_rank = float(np.sum(dabs <= dabs[-1])) / rank_win if len(dabs) >= rank_win else 0.5
    return _safe((1.0 - vol_rank) + (1.0 - d_rank))


def _alpha39(close: np.ndarray, volume: np.ndarray,
             delta_win: int = 7, adv_win: int = 20,
             decay_win: int = 9, rank_win: int = 60) -> Optional[float]:
    """Drop on low relative volume = bounce setup (positive)."""
    if len(close) < rank_win + delta_win:
        return None
    deltas = np.array([close[i] - close[i - delta_win]
                       for i in range(delta_win, len(close))])[-rank_win:]
    d_rank = float(np.sum(deltas <= deltas[-1])) / rank_win
    comp1 = -d_rank
    adv20 = float(np.mean(volume[-adv_win:]))
    ratio = volume[-decay_win:] / max(adv20, 1e-10)
    w = np.arange(1, decay_win + 1, dtype=float)
    w /= w.sum()
    decay_val = float(np.dot(w, ratio[-decay_win:]))
    comp2 = 1.0 - min(decay_val, 2.0) / 2.0
    return _safe(comp1 * comp2)


def _alpha41(high: np.ndarray, low: np.ndarray, vwap: np.ndarray) -> Optional[float]:
    """Geometric midpoint vs VWAP. Pos = buying pressure above VWAP."""
    if len(high) < 1:
        return None
    geo_mid = float(np.sqrt(max(high[-1] * low[-1], 0)))
    return _safe(geo_mid - float(vwap[-1]))


def _alpha49(close: np.ndarray, threshold: float = -0.1) -> Optional[float]:
    """Velocity acceleration: +1 if recent momentum accelerated, else mean-revert."""
    if len(close) < 21:
        return None
    vel_old = (float(close[-11]) - float(close[-21])) / 10.0
    vel_new = (float(close[-1])  - float(close[-11])) / 10.0
    if (vel_old - vel_new) < threshold:
        return 1.0
    return _safe(-(close[-1] - close[-2]))


def _alpha50(high: np.ndarray, volume: np.ndarray,
             avg_win: int = 20, corr_win: int = 5) -> Optional[float]:
    """Highs suppressed while volume rises = distribution (positive = bearish)."""
    if len(high) < avg_win + corr_win:
        return None
    avg_h = float(np.mean(high[-avg_win:]))
    deficit = avg_h - float(high[-1])
    deficit_series = np.array([np.mean(high[i - avg_win + 1: i + 1]) - high[i]
                                for i in range(avg_win - 1, len(high))])[-avg_win:]
    d_rank = float(np.sum(deficit_series <= deficit)) / avg_win
    h_sl = high[-corr_win:].astype(float)
    v_sl = volume[-corr_win:].astype(float)
    if np.std(h_sl) < 1e-10 or np.std(v_sl) < 1e-10:
        c_rank = 0.5
    else:
        c = float(np.corrcoef(h_sl, v_sl)[0, 1])
        c_arr = np.array([float(np.corrcoef(high[i: i + corr_win],
                                             volume[i: i + corr_win])[0, 1])
                           for i in range(len(high) - corr_win)])
        c_rank = float(np.sum(c_arr <= c)) / len(c_arr) if len(c_arr) > 0 else 0.5
    return _safe(-d_rank * c_rank)


def _alpha55(close: np.ndarray, high: np.ndarray, low: np.ndarray,
             volume: np.ndarray, hl_win: int = 12, corr_win: int = 6) -> Optional[float]:
    """Close-in-range vs volume: quiet near-high = positive (breakout potential)."""
    if len(close) < hl_win + corr_win:
        return None
    norm_pos = []
    for i in range(hl_win - 1, len(close)):
        h = float(np.max(high[i - hl_win + 1: i + 1]))
        l = float(np.min(low[i - hl_win + 1: i + 1]))
        rng = h - l
        norm_pos.append((close[i] - l) / rng if rng > 1e-10 else 0.5)
    np_sl = np.array(norm_pos[-corr_win:])
    v_sl  = volume[-corr_win:].astype(float)
    if np.std(np_sl) < 1e-10 or np.std(v_sl) < 1e-10:
        return 0.0
    return _safe(-float(np.corrcoef(np_sl, v_sl)[0, 1]))


def _alpha101(open_: np.ndarray, high: np.ndarray,
              low: np.ndarray, close: np.ndarray) -> Optional[float]:
    """Bar quality ratio: (close-open)/(high-low+0.001). Range ≈ [-1, +1]."""
    if len(close) < 1:
        return None
    body  = float(close[-1] - open_[-1])
    range_ = float(high[-1] - low[-1]) + 0.001
    return _safe(body / range_)


def calc_alpha101(open_: np.ndarray, high: np.ndarray, low: np.ndarray,
                  close: np.ndarray, volume: np.ndarray,
                  vwap_series: np.ndarray) -> dict:
    """Compute all 20 WorldQuant Alpha101 signals. Returns flat dict of scalars."""
    ret = compute_returns(close)
    # Pad returns to same length as close (prepend NaN for first bar)
    ret_full = np.concatenate([[float("nan")], ret])
    return {
        "a1_momentum_peak":    _safe(_alpha1(close, ret_full)),
        "a2_vol_accel_corr":   _safe(_alpha2(open_, close, volume)),
        "a3_open_vol_ranked":  _safe(_alpha3(open_, volume)),
        "a4_support_floor":    _safe(_alpha4(low)),
        "a6_open_vol_raw":     _safe(_alpha6(open_, volume)),
        "a7_vol_gated":        _safe(_alpha7(close, volume)),
        "a9_regime_5d":        _safe(_alpha9(close)),
        "a10_regime_4d":       _safe(_alpha10(close)),
        "a12_capitulation":    _safe(_alpha12(close, volume)),
        "a20_gap_structure":   _safe(_alpha20(open_, high, low, close)),
        "a27_vwap_participation": _safe(_alpha27(volume, vwap_series)),
        "a31_mean_rev":        _safe(_alpha31(close, low, volume)),
        "a32_vwap_persist":    _safe(_alpha32(close, vwap_series)),
        "a34_vol_squeeze":     _safe(_alpha34(close, ret_full[1:])),
        "a39_low_vol_drop":    _safe(_alpha39(close, volume)),
        "a41_geo_mid_vwap":    _safe(_alpha41(high, low, vwap_series)),
        "a49_accel":           _safe(_alpha49(close)),
        "a50_distribution":    _safe(_alpha50(high, volume)),
        "a55_range_vol_corr":  _safe(_alpha55(close, high, low, volume)),
        "a101_bar_quality":    _safe(_alpha101(open_, high, low, close)),
    }
