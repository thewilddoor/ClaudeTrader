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
