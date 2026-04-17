# tests/test_tools/test_ta.py
import numpy as np
import pytest
from datetime import date, timedelta


@pytest.fixture
def ohlcv_260():
    """260 trading days of synthetic OHLCV. Oldest-first."""
    np.random.seed(42)
    n = 260
    close = 100.0 + np.cumsum(np.random.randn(n) * 0.5)
    close = np.maximum(close, 10.0)
    high = close + np.abs(np.random.randn(n)) * 1.5
    low = np.maximum(close - np.abs(np.random.randn(n)) * 1.5, 1.0)
    open_ = low + np.random.rand(n) * (high - low)
    volume = np.random.randint(1_000_000, 50_000_000, n).astype(float)
    dates = []
    d = date(2025, 7, 1)
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    return {"dates": dates, "open": open_, "high": high, "low": low,
            "close": close, "volume": volume}


# ── Utility tests ──────────────────────────────────────────────────────────

def test_nan_to_none_converts_nan():
    from scheduler.tools._ta import _nan_to_none
    assert _nan_to_none(float("nan")) is None
    assert _nan_to_none(float("inf")) is None
    assert _nan_to_none(42.5) == 42.5
    assert _nan_to_none(None) is None


def test_ts_rank_pct_max_is_one(ohlcv_260):
    from scheduler.tools._ta import ts_rank_pct
    arr = ohlcv_260["close"]
    result = ts_rank_pct(arr, window=20)
    # Warmup: first 19 values are NaN
    assert all(np.isnan(result[:19]))
    # All non-NaN values in [0, 1]
    valid = result[~np.isnan(result)]
    assert np.all(valid >= 0.0) and np.all(valid <= 1.0)


def test_ts_rank_pct_last_is_one_when_max(ohlcv_260):
    from scheduler.tools._ta import ts_rank_pct
    arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ts_rank_pct(arr, window=5)
    assert result[-1] == 1.0  # 5.0 is max of window


def test_decay_linear_weights_recent_higher():
    from scheduler.tools._ta import decay_linear
    arr = np.array([1.0, 1.0, 1.0, 1.0, 10.0])  # spike at end
    result = decay_linear(arr, window=5)
    # With linear decay, most recent (10.0) gets weight 5/15
    assert result > 3.0  # simple mean would be 2.8


def test_compute_returns_length(ohlcv_260):
    from scheduler.tools._ta import compute_returns
    close = ohlcv_260["close"]
    ret = compute_returns(close)
    assert len(ret) == len(close) - 1


def test_resample_weekly_groups_correctly():
    from scheduler.tools._ta import resample_weekly
    # 5 Mon-Fri records in one week + 3 in next week
    records = [
        {"date": "2026-04-13", "open": 100.0, "high": 105.0, "low": 98.0, "close": 103.0, "volume": 1e6},
        {"date": "2026-04-14", "open": 103.0, "high": 107.0, "low": 101.0, "close": 104.0, "volume": 2e6},
        {"date": "2026-04-15", "open": 104.0, "high": 108.0, "low": 100.0, "close": 102.0, "volume": 1.5e6},
        {"date": "2026-04-16", "open": 102.0, "high": 106.0, "low": 100.0, "close": 105.0, "volume": 1.2e6},
        {"date": "2026-04-17", "open": 105.0, "high": 109.0, "low": 103.0, "close": 106.0, "volume": 1.8e6},
        {"date": "2026-04-20", "open": 106.0, "high": 110.0, "low": 104.0, "close": 108.0, "volume": 2e6},
    ]
    weeks = resample_weekly(records)
    assert len(weeks) == 2
    w1 = weeks[0]
    assert w1["d"] == "2026-04-13"   # Monday
    assert w1["o"] == 100.0          # first open
    assert w1["h"] == 109.0          # max high
    assert w1["l"] == 98.0           # min low
    assert w1["c"] == 106.0          # last close
    assert w1["v"] == pytest.approx(7.5e6)  # sum volume
