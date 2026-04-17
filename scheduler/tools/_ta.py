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
