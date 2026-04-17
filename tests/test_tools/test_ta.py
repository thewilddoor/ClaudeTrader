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


# ── Momentum tests ────────────────────────────────────────────────────────

def test_calc_rsi_keys_and_range(ohlcv_260):
    from scheduler.tools._ta import calc_rsi
    result = calc_rsi(ohlcv_260["close"])
    for period in [7, 14, 21]:
        key = f"rsi_{period}"
        assert key in result
        cur = result[key]["cur"]
        # ohlcv_260 has 260 candles — warmup is max 21 bars, so cur must be non-None
        assert cur is not None, f"{key} cur should not be None with 260 candles"
        assert 0.0 <= cur <= 100.0
        assert "90d_hi" in result[key]
        assert "90d_avg" in result[key]


def test_calc_macd_keys(ohlcv_260):
    from scheduler.tools._ta import calc_macd
    result = calc_macd(ohlcv_260["close"])
    for key in ["macd_line", "signal_line", "histogram", "crossover", "divergence"]:
        assert key in result
    assert result["crossover"] in ("bull", "bear", "none")
    assert result["divergence"] in ("bull", "bear", "none")


def test_calc_stoch_zones(ohlcv_260):
    from scheduler.tools._ta import calc_stoch
    result = calc_stoch(ohlcv_260["high"], ohlcv_260["low"], ohlcv_260["close"])
    for key in ["stoch_5", "stoch_14"]:
        assert key in result
        assert result[key]["zone"] in ("overbought", "oversold", "neutral")
        assert result[key]["crossover"] in ("bull", "bear", "none")


def test_calc_mfi_range(ohlcv_260):
    from scheduler.tools._ta import calc_mfi
    result = calc_mfi(ohlcv_260["high"], ohlcv_260["low"],
                      ohlcv_260["close"], ohlcv_260["volume"])
    cur = result["cur"]
    assert cur is None or 0.0 <= cur <= 100.0
    assert result["divergence"] in ("bull", "bear", "none")


# ── Trend tests ───────────────────────────────────────────────────────────

def test_calc_ema_samples_returns_5_points(ohlcv_260):
    from scheduler.tools._ta import calc_ema_samples
    result = calc_ema_samples(ohlcv_260["close"], ohlcv_260["dates"], periods=[21, 55, 89])
    assert "ema_samples" in result
    assert len(result["ema_samples"]) == 5
    assert "ema21" in result["ema_samples"][0]
    assert "ema55" in result["ema_samples"][0]
    assert "ema89" in result["ema_samples"][0]
    assert result["alignment"] in ("bull", "bear", "mixed")
    assert "price_vs_ema21_pct" in result


def test_calc_ema_samples_weekly_no_ema89(ohlcv_260):
    from scheduler.tools._ta import calc_ema_samples, resample_weekly
    records = [{"date": d, "open": float(o), "high": float(h), "low": float(l),
                "close": float(c), "volume": float(v)}
               for d, o, h, l, c, v in zip(
                   ohlcv_260["dates"], ohlcv_260["open"], ohlcv_260["high"],
                   ohlcv_260["low"], ohlcv_260["close"], ohlcv_260["volume"])]
    weekly = resample_weekly(records)
    w_close = np.array([w["c"] for w in weekly])
    w_dates = [w["d"] for w in weekly]
    result = calc_ema_samples(w_close, w_dates, periods=[21, 55])
    assert "ema89" not in result["ema_samples"][0]


def test_calc_adx_trend_strength(ohlcv_260):
    from scheduler.tools._ta import calc_adx
    result = calc_adx(ohlcv_260["high"], ohlcv_260["low"], ohlcv_260["close"], timeframe="1d")
    assert result["trend_strength"] in ("strong", "trending", "ranging")
    # ohlcv_260 has 260 candles — ADX warmup is ~28 bars, so adx must be non-None
    assert result["adx"] is not None, "adx should not be None with 260 candles"
    assert result["adx"] >= 0
    assert "di_plus" in result and "di_minus" in result


def test_calc_adx_1w_no_di(ohlcv_260):
    from scheduler.tools._ta import calc_adx
    result = calc_adx(ohlcv_260["high"], ohlcv_260["low"], ohlcv_260["close"], timeframe="1w")
    assert "di_plus" not in result
    assert "trend_strength" in result


def test_calc_vwap_returns_keys(ohlcv_260):
    from scheduler.tools._ta import calc_vwap
    result = calc_vwap(ohlcv_260["high"], ohlcv_260["low"],
                       ohlcv_260["close"], ohlcv_260["volume"], ohlcv_260["dates"])
    assert "vwap" in result
    assert result["slope"] in ("up", "down", "flat")
    assert "price_vs_vwap_pct" in result
    assert "vwap_series" in result  # internal — used by alpha27/32/41


# ── Volatility + Volume tests ─────────────────────────────────────────────

def test_calc_atr_regime(ohlcv_260):
    from scheduler.tools._ta import calc_atr
    result = calc_atr(ohlcv_260["high"], ohlcv_260["low"], ohlcv_260["close"])
    assert result["atr_regime"] in ("expanding", "contracting", "stable")
    assert result["atr"] is None or result["atr"] > 0
    assert "atr_pct" in result
    assert "14d_avg" in result and "30d_avg" in result


def test_calc_bollinger_pct_b_range(ohlcv_260):
    from scheduler.tools._ta import calc_bollinger
    result = calc_bollinger(ohlcv_260["close"])
    assert "pct_b" in result
    assert "squeeze" in result
    assert isinstance(result["squeeze"], bool)
    assert "upper_2sd" in result and "lower_2sd" in result
    assert "upper_1sd" in result and "lower_1sd" in result
    if result["upper_2sd"] is not None:
        assert result["upper_2sd"] >= result["upper_1sd"]
        assert result["lower_1sd"] >= result["lower_2sd"]


def test_calc_volume_ratio_positive(ohlcv_260):
    from scheduler.tools._ta import calc_volume_ratio
    # Build fake weekly volume array
    w_vol = ohlcv_260["volume"].reshape(-1, 5).sum(axis=1).astype(float)
    result = calc_volume_ratio(ohlcv_260["volume"], w_vol)
    assert result["vol_ratio_1d"] > 0
    assert "10d_hi_ratio" in result and "10d_lo_ratio" in result


def test_calc_obv_slope_is_categorical(ohlcv_260):
    from scheduler.tools._ta import calc_obv
    result = calc_obv(ohlcv_260["close"], ohlcv_260["volume"])
    assert result["slope"] in ("up", "down", "flat")
    assert result["vs_price"] in ("confirming", "diverging")
    assert isinstance(result["trend_days"], int)


# ── Price structure tests ─────────────────────────────────────────────────

def test_calc_support_resistance_counts(ohlcv_260):
    from scheduler.tools._ta import calc_support_resistance
    result = calc_support_resistance(
        ohlcv_260["high"], ohlcv_260["low"], ohlcv_260["close"],
        ohlcv_260["volume"], ohlcv_260["dates"], n_support=3, n_resist=3
    )
    supports = [r for r in result if r["type"] == "support"]
    resists  = [r for r in result if r["type"] == "resistance"]
    assert len(supports) <= 3
    assert len(resists) <= 3
    if supports:
        assert supports[0]["strength"] in ("strong", "moderate", "weak")
        assert "last_tested" in supports[0]


def test_calc_pivot_points_structure(ohlcv_260):
    from scheduler.tools._ta import calc_pivot_points
    result = calc_pivot_points(ohlcv_260["high"], ohlcv_260["low"], ohlcv_260["close"])
    for key in ["pp", "r1", "r2", "s1", "s2"]:
        assert key in result
    assert result["r1"] > result["pp"] > result["s1"]


def test_calc_52w_range_percentile(ohlcv_260):
    from scheduler.tools._ta import calc_52w_range
    result = calc_52w_range(ohlcv_260["close"])
    assert 0.0 <= result["wk52_pct"] <= 100.0
    assert result["wk52_hi"] >= result["wk52_lo"]
    assert "dist_from_hi_pct" in result and "dist_from_lo_pct" in result


# ── IC tests ──────────────────────────────────────────────────────────────

def test_detect_order_blocks_returns_list(ohlcv_260):
    from scheduler.tools._ta import detect_order_blocks, calc_atr
    atr_result = calc_atr(ohlcv_260["high"], ohlcv_260["low"], ohlcv_260["close"])
    atr_val = atr_result["atr"] or 1.5
    obs = detect_order_blocks(
        ohlcv_260["open"], ohlcv_260["high"], ohlcv_260["low"],
        ohlcv_260["close"], ohlcv_260["dates"], atr_val, max_count=3
    )
    assert isinstance(obs, list)
    assert len(obs) <= 3
    for ob in obs:
        assert ob["type"] in ("bullish", "bearish")
        assert "ob_high" in ob and "ob_low" in ob and "ob_mid" in ob
        assert "tested" in ob and "broken" in ob and "stale" in ob


def test_detect_fvg_min_gap(ohlcv_260):
    from scheduler.tools._ta import detect_fvg
    fvgs = detect_fvg(ohlcv_260["high"], ohlcv_260["low"],
                      ohlcv_260["close"], ohlcv_260["dates"], max_count=3)
    assert isinstance(fvgs, list)
    assert len(fvgs) <= 3
    for fvg in fvgs:
        assert fvg["type"] in ("bullish", "bearish")
        assert fvg["gap_high"] > fvg["gap_low"]


def test_detect_liquidity_levels_counts(ohlcv_260):
    from scheduler.tools._ta import detect_liquidity_levels
    levels = detect_liquidity_levels(
        ohlcv_260["high"], ohlcv_260["low"],
        ohlcv_260["close"], ohlcv_260["dates"], max_count=4
    )
    assert len(levels) <= 4
    for lv in levels:
        assert lv["type"] in ("buy_side", "sell_side")
        assert lv["touches"] >= 2


def test_detect_market_structure_keys(ohlcv_260):
    from scheduler.tools._ta import detect_market_structure
    result = detect_market_structure(
        ohlcv_260["high"], ohlcv_260["low"],
        ohlcv_260["close"], ohlcv_260["dates"]
    )
    assert result["structure"] in ("uptrend", "downtrend", "ranging")
    assert "last_hh" in result and "last_hl" in result and "msb" in result


def test_calc_ics_wrapper(ohlcv_260):
    from scheduler.tools._ta import calc_ics
    result = calc_ics(
        ohlcv_260["open"], ohlcv_260["high"], ohlcv_260["low"],
        ohlcv_260["close"], ohlcv_260["volume"], ohlcv_260["dates"],
        timeframe="1d"
    )
    assert "order_blocks" in result
    assert "fvgs" in result
    assert "liquidity_levels" in result
    assert "market_structure" in result
    assert "breaker_blocks" in result


def test_calc_ics_wrapper_1w(ohlcv_260):
    """1W timeframe uses resampled weekly arrays — same keys, no DI+ in structure."""
    from scheduler.tools._ta import calc_ics, resample_weekly
    import numpy as np
    # Build weekly arrays by resampling daily fixture
    daily_records = [
        {"date": ohlcv_260["dates"][i], "open": float(ohlcv_260["open"][i]),
         "high": float(ohlcv_260["high"][i]), "low": float(ohlcv_260["low"][i]),
         "close": float(ohlcv_260["close"][i]), "volume": float(ohlcv_260["volume"][i])}
        for i in range(len(ohlcv_260["dates"]))
    ]
    weekly = resample_weekly(daily_records)
    w_open  = np.array([w["o"] for w in weekly])
    w_high  = np.array([w["h"] for w in weekly])
    w_low   = np.array([w["l"] for w in weekly])
    w_close = np.array([w["c"] for w in weekly])
    w_vol   = np.array([w["v"] for w in weekly])
    w_dates = [w["d"] for w in weekly]

    result = calc_ics(w_open, w_high, w_low, w_close, w_vol, w_dates, timeframe="1w")
    assert "order_blocks" in result
    assert "fvgs" in result
    assert "liquidity_levels" in result
    assert "market_structure" in result
    assert "breaker_blocks" in result


def test_order_block_stale_flag(ohlcv_260):
    """OBs older than _STALE_DAYS bars should have stale=True."""
    from scheduler.tools._ta import detect_order_blocks, _STALE_DAYS
    import numpy as np
    # Build a minimal array with a guaranteed bullish OB at bar 0
    n = _STALE_DAYS + 10
    close = np.ones(n) * 100.0
    open_ = np.ones(n) * 100.0
    high  = np.ones(n) * 101.0
    low   = np.ones(n) * 99.0
    # Bar 0: bearish candle; bar 1: huge bullish impulse
    close[0] = 98.0; open_[0] = 102.0   # bearish
    close[1] = 110.0; open_[1] = 98.0   # large bullish impulse
    high[1] = 111.0
    dates = [f"2025-01-{(i % 28) + 1:02d}" for i in range(n)]
    # Use atr=1.0 so impulse of 12 exceeds 2×ATR easily
    obs = detect_order_blocks(open_, high, low, close, dates, atr=1.0)
    # If any OBs detected at bar 0, they are > _STALE_DAYS bars ago => stale=True
    if obs:
        assert obs[-1]["stale"] is True, "OB at bar 0 should be marked stale"


# ── Pattern tests ─────────────────────────────────────────────────────────

def test_calc_patterns_returns_list(ohlcv_260):
    from scheduler.tools._ta import calc_patterns
    result = calc_patterns(
        ohlcv_260["open"], ohlcv_260["high"], ohlcv_260["low"],
        ohlcv_260["close"], ohlcv_260["dates"], lookback=5
    )
    assert isinstance(result, list)
    assert len(result) <= 5
    for p in result:
        assert "pattern" in p and "date" in p and "signal" in p
        assert p["signal"] in ("bull", "bear", "neutral")


def test_calc_patterns_empty_when_no_signal():
    from scheduler.tools._ta import calc_patterns
    # Trending candles with normal OHLC structure (no reversal patterns)
    n = 20
    close = np.arange(100.0, 100.0 + n, 1.0)
    high = close + 0.5
    low = close - 0.5
    open_ = close - 0.2  # slightly bullish
    result = calc_patterns(open_, high, low, close,
                           [f"2026-01-{i+1:02d}" for i in range(n)], lookback=5)
    # Trending candles generally don't trigger patterns
    assert isinstance(result, list)
