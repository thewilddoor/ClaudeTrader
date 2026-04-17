import json
import responses
import pytest
from datetime import date, timedelta
from scheduler.tools.fmp import fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar


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
def test_fmp_ohlcv_returns_enriched_payload():
    """fmp_ohlcv now returns an enriched TA payload, not raw OHLCV."""
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/stable/historical-price-eod/full",
        json=_make_fmp_records(260),
        status=200,
    )
    result = fmp_ohlcv(ticker="AAPL", limit=5, api_key="test")

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
def test_fmp_ohlcv_fallback_on_insufficient_data():
    """Returns error dict when FMP returns fewer than 30 candles."""
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/stable/historical-price-eod/full",
        json=_make_fmp_records(10),
        status=200,
    )
    result = fmp_ohlcv(ticker="TINY", limit=5, api_key="test")
    assert "error" in result
    assert result["error"] == "insufficient_data"
    assert "raw_ohlcv" in result  # partial candles still returned for context


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
