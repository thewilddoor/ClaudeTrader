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
    """Screen US stocks by market cap and volume.

    Args:
        market_cap_more_than: Minimum market cap in USD (default 1 billion).
        volume_more_than: Minimum average daily volume (default 500 thousand).
        exchange: Comma-separated list of exchanges to include (e.g. NYSE,NASDAQ).
        limit: Maximum number of results to return.
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        list: Matching stock records with symbol, price, volume, marketCap fields.
    """
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get("/company-screener", {
        "marketCapMoreThan": market_cap_more_than,
        "volumeMoreThan": volume_more_than,
        "exchange": exchange,
        "limit": limit,
    }, api_key)


def fmp_ohlcv(ticker: str, limit: int = 90, api_key: Optional[str] = None) -> dict:
    """Get daily OHLCV price data for a stock ticker.

    Args:
        ticker: Stock ticker symbol (e.g. AAPL, MSFT).
        limit: Number of trading days of history to return (default 90).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        dict: Contains 'symbol' string and 'historical' list of daily OHLCV records.
    """
    api_key = api_key or os.environ["FMP_API_KEY"]
    result = _get("/historical-price-eod/full", {"symbol": ticker, "limit": limit}, api_key)
    if isinstance(result, list):
        return {"symbol": ticker, "historical": result}
    return result


def fmp_news(tickers: list, limit: int = 10, api_key: Optional[str] = None) -> list:
    """Get recent news articles for a list of stock tickers.

    Args:
        tickers: List of ticker symbols to fetch news for (e.g. ['AAPL', 'MSFT']).
        limit: Maximum number of news articles to return per ticker.
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        list: News article records with title, text, url, publishedDate fields.
    """
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get("/news/stock", {"symbols": ",".join(tickers), "limit": limit}, api_key)


def fmp_earnings_calendar(from_date: str, to_date: str, api_key: Optional[str] = None) -> list:
    """Get scheduled earnings announcements between two dates.

    Args:
        from_date: Start date in YYYY-MM-DD format (e.g. 2026-04-10).
        to_date: End date in YYYY-MM-DD format (e.g. 2026-04-17).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        list: Earnings events with symbol, date, epsEstimated, revenueEstimated fields.
    """
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get("/earnings-calendar", {"from": from_date, "to": to_date}, api_key)
