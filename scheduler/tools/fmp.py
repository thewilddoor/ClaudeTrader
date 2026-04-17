"""
Financial Modeling Prep (FMP) market data tools.

IMPORTANT: Each function must be fully self-contained (imports, helpers inlined)
because Letta's upsert_from_function extracts only the function body and runs it
in an isolated sandbox with no access to module-level code.
"""
from typing import Optional


def fmp_screener(
    market_cap_more_than: int = 1_000_000_000,
    volume_more_than: int = 500_000,
    exchange: str = "NYSE,NASDAQ",
    limit: int = 50,
    api_key: Optional[str] = None,
) -> list:
    """Screen US stocks by market cap and volume.

    Args:
        market_cap_more_than: Minimum market cap in USD (default 1 billion, changeable).
        volume_more_than: Minimum average daily volume (default 500 thousand, changeable).
        exchange: Comma-separated exchanges to include (default NYSE,NASDAQ, changeable).
        limit: Maximum number of results to return (default 50, changeable).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        list: Matching stock records with symbol, price, volume, marketCap fields.
    """
    import os
    import requests

    api_key = api_key or os.environ["FMP_API_KEY"]
    params = {
        "marketCapMoreThan": market_cap_more_than,
        "volumeMoreThan": volume_more_than,
        "exchange": exchange,
        "limit": limit,
        "apikey": api_key,
    }
    response = requests.get("https://financialmodelingprep.com/stable/company-screener", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fmp_ohlcv(ticker: str, limit: int = 20, api_key: Optional[str] = None) -> dict:
    """Get daily OHLCV price data for a stock ticker.

    Args:
        ticker: Stock ticker symbol (e.g. AAPL, MSFT).
        limit: Number of trading days of history to return (default 20, changeable).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        dict: Contains 'symbol' string and 'historical' list of daily OHLCV records.
    """
    import os
    import requests

    api_key = api_key or os.environ["FMP_API_KEY"]
    params = {"symbol": ticker, "limit": limit, "apikey": api_key}
    response = requests.get("https://financialmodelingprep.com/stable/historical-price-eod/full", params=params, timeout=30)
    response.raise_for_status()
    result = response.json()
    if isinstance(result, list):
        return {"symbol": ticker, "historical": result}
    return result


def fmp_news(tickers: list, limit: int = 10, api_key: Optional[str] = None) -> list:
    """Get recent news articles for a list of stock tickers.

    Args:
        tickers: List of ticker symbols to fetch news for (e.g. ['AAPL', 'MSFT']).
        limit: Maximum number of news articles to return per ticker (default 10, changeable).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        list: News article records with title, text, url, publishedDate fields.
    """
    import os
    import requests

    api_key = api_key or os.environ["FMP_API_KEY"]
    params = {"symbols": ",".join(tickers), "limit": limit, "apikey": api_key}
    response = requests.get("https://financialmodelingprep.com/stable/news/stock", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fmp_earnings_calendar(from_date: str, to_date: str, api_key: Optional[str] = None) -> list:
    """Get scheduled earnings announcements between two dates.

    Args:
        from_date: Start date in YYYY-MM-DD format (e.g. 2026-04-10).
        to_date: End date in YYYY-MM-DD format (e.g. 2026-04-17).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        list: Earnings events with symbol, date, epsEstimated, revenueEstimated fields.
    """
    import os
    import requests

    api_key = api_key or os.environ["FMP_API_KEY"]
    params = {"from": from_date, "to": to_date, "apikey": api_key}
    response = requests.get("https://financialmodelingprep.com/stable/earnings-calendar", params=params, timeout=30)
    response.raise_for_status()
    return response.json()
