import os
import requests
from typing import Optional

FMP_BASE = "https://financialmodelingprep.com/stable"


def _get(endpoint: str, params: dict, api_key: str) -> dict | list:
    params["apikey"] = api_key
    response = requests.get(f"{FMP_BASE}{endpoint}", params=params, timeout=10)
    response.raise_for_status()
    return response.json()


def fmp_screener(
    market_cap_more_than: int = 1_000_000_000,
    volume_more_than: int = 500_000,
    exchange: str = "NYSE,NASDAQ",
    limit: int = 50,
    api_key: Optional[str] = None,
) -> list:
    """Screen US stocks by market cap and volume. Returns list of matching stocks."""
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get("/company-screener", {
        "marketCapMoreThan": market_cap_more_than,
        "volumeMoreThan": volume_more_than,
        "exchange": exchange,
        "limit": limit,
    }, api_key)


def fmp_ohlcv(ticker: str, limit: int = 90, api_key: Optional[str] = None) -> dict:
    """Get daily OHLCV data for a ticker. Returns dict with 'historical' list."""
    api_key = api_key or os.environ["FMP_API_KEY"]
    result = _get("/historical-price-eod/full", {"symbol": ticker, "limit": limit}, api_key)
    # Stable API returns a flat list; normalise to {"symbol": ticker, "historical": [...]}
    if isinstance(result, list):
        return {"symbol": ticker, "historical": result}
    return result


def fmp_news(tickers: list[str], limit: int = 10, api_key: Optional[str] = None) -> list:
    """Get recent news for a list of tickers."""
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get("/news/stock", {"symbols": ",".join(tickers), "limit": limit}, api_key)


def fmp_earnings_calendar(from_date: str, to_date: str, api_key: Optional[str] = None) -> list:
    """Get earnings announcements between two dates (YYYY-MM-DD format)."""
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get("/earnings-calendar", {"from": from_date, "to": to_date}, api_key)
