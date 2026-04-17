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
