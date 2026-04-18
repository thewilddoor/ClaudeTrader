import json
import responses
import pytest
from datetime import date, timedelta
from scheduler.tools.fmp import fmp_screener, fmp_ta, fmp_check_current_price, fmp_news, fmp_earnings_calendar


@responses.activate
def test_fmp_screener_returns_list():
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/stable/company-screener",
        json=[{"symbol": "AAPL", "marketCap": 3000000000000, "volume": 60000000}],
        status=200,
    )
    result = fmp_screener(market_cap_more_than=1000000000, volume_more_than=500000, api_key="test")
    assert isinstance(result, list)
    assert result[0]["symbol"] == "AAPL"


def _make_fmp_records(n: int = 260) -> list:
    """Generate n synthetic FMP daily records, newest-first (FMP convention)."""
    import random
    random.seed(7)
    records = []
    base = date(2025, 7, 1)
    trading_days = []
    d = base
    while len(trading_days) < n:
        if d.weekday() < 5:
            trading_days.append(d)
        d += timedelta(days=1)
    close = 150.0
    for day in reversed(trading_days):  # newest first
        close += random.gauss(0, 0.8)
        close = max(close, 10.0)
        high  = close + abs(random.gauss(0, 1.2))
        low   = max(close - abs(random.gauss(0, 1.2)), 1.0)
        open_ = low + random.random() * (high - low)
        records.append({
            "date": day.isoformat(), "open": round(open_, 2),
            "high": round(high, 2), "low": round(low, 2),
            "close": round(close, 2), "volume": random.randint(5_000_000, 60_000_000),
        })
    return records


@responses.activate
def test_fmp_ta_returns_enriched_payload():
    """fmp_ta returns an enriched TA payload across all 14 keys."""
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/stable/historical-price-eod/full",
        json=_make_fmp_records(260),
        status=200,
    )
    result = fmp_ta(ticker="AAPL", limit=5, api_key="test")

    # Top-level structure
    assert "meta" in result
    assert result["meta"]["symbol"] == "AAPL"
    assert "ohlcv_1d" in result and len(result["ohlcv_1d"]) == 5
    assert "ohlcv_1w" in result and len(result["ohlcv_1w"]) == 5
    assert "momentum_1d" in result
    assert "trend_1d" in result
    assert "trend_1w" in result
    assert "volatility_1d" in result
    assert "volume_1d" in result
    assert "price_structure" in result
    assert "ics_1d" in result
    assert "ics_1w" in result
    assert "patterns_1d" in result
    assert "patterns_1w" in result
    assert "alpha101" in result

    # Spot-check key alpha101 keys
    assert "a101_bar_quality" in result["alpha101"]
    assert "a12_capitulation" in result["alpha101"]

    # Spot-check RSI
    assert "rsi_7" in result["momentum_1d"]
    assert "rsi_14" in result["momentum_1d"]

    # Raw candle keys abbreviated
    candle = result["ohlcv_1d"][0]
    assert set(candle.keys()) == {"d", "o", "h", "l", "c", "v"}


@responses.activate
def test_fmp_ta_fallback_on_insufficient_data():
    """Returns error dict when FMP returns fewer than 30 candles."""
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/stable/historical-price-eod/full",
        json=_make_fmp_records(10),
        status=200,
    )
    result = fmp_ta(ticker="TINY", limit=5, api_key="test")
    assert "error" in result
    assert result["error"] == "insufficient_data"
    assert "raw_ohlcv" in result  # partial candles still returned for context


@responses.activate
def test_fmp_check_current_price_returns_snapshot():
    """fmp_check_current_price returns lightweight price snapshot."""
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/stable/quote",
        json=[{
            "symbol": "NVDA",
            "price": 203.44,
            "open": 201.22,
            "dayHigh": 205.88,
            "dayLow": 200.44,
            "previousClose": 198.92,
            "volume": 28400000,
            "avgVolume": 42000000,
        }],
        status=200,
    )
    result = fmp_check_current_price(ticker="NVDA", api_key="test")

    assert result["symbol"] == "NVDA"
    assert result["price"] == 203.44
    assert result["open"] == 201.22
    assert result["day_high"] == 205.88
    assert result["day_low"] == 200.44
    assert result["prev_close"] == 198.92
    assert result["volume"] == 28400000
    assert result["avg_volume"] == 42000000
    assert result["vol_ratio"] == round(28400000 / 42000000, 2)
    assert abs(result["change_pct"] - round((203.44 - 198.92) / 198.92, 4)) < 1e-6

    # Must NOT contain any TA fields
    assert "momentum_1d" not in result
    assert "alpha101" not in result
    assert "ics_1d" not in result


@responses.activate
def test_fmp_news_returns_list():
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/stable/news/stock",
        json=[{"title": "Apple beats earnings", "symbol": "AAPL"}],
        status=200,
    )
    result = fmp_news(tickers=["AAPL"], limit=1, api_key="test")
    assert isinstance(result, list)
    assert result[0]["symbol"] == "AAPL"


@responses.activate
def test_fmp_earnings_calendar_returns_list():
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/stable/earnings-calendar",
        json=[{"symbol": "AAPL", "date": "2026-04-30"}],
        status=200,
    )
    result = fmp_earnings_calendar(from_date="2026-04-10", to_date="2026-04-30", api_key="test")
    assert isinstance(result, list)
    assert result[0]["symbol"] == "AAPL"
