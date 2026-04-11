import responses
import pytest
from scheduler.tools.fmp import fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar


@responses.activate
def test_fmp_screener_returns_list():
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/api/v3/stock-screener",
        json=[{"symbol": "AAPL", "marketCap": 3000000000000, "volume": 60000000}],
        status=200,
    )
    result = fmp_screener(market_cap_more_than=1000000000, volume_more_than=500000, api_key="test")
    assert isinstance(result, list)
    assert result[0]["symbol"] == "AAPL"


@responses.activate
def test_fmp_ohlcv_returns_dataframe_dict():
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/api/v3/historical-price-full/AAPL",
        json={"historical": [{"date": "2026-04-09", "open": 200.0, "high": 205.0, "low": 198.0, "close": 203.0, "volume": 55000000}]},
        status=200,
    )
    result = fmp_ohlcv(ticker="AAPL", limit=1, api_key="test")
    assert "historical" in result
    assert result["historical"][0]["close"] == 203.0


@responses.activate
def test_fmp_news_returns_list():
    responses.add(
        responses.GET,
        "https://financialmodelingprep.com/api/v3/stock_news",
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
        "https://financialmodelingprep.com/api/v3/earning_calendar",
        json=[{"symbol": "AAPL", "date": "2026-04-30"}],
        status=200,
    )
    result = fmp_earnings_calendar(from_date="2026-04-10", to_date="2026-04-30", api_key="test")
    assert isinstance(result, list)
    assert result[0]["symbol"] == "AAPL"
