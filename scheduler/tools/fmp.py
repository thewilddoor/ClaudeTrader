# scheduler/tools/fmp.py
"""Financial Modeling Prep (FMP) market data tools."""
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


def fmp_ohlcv(ticker: str, limit: int = 5, api_key: Optional[str] = None) -> dict:
    """Get a professional technical analysis payload for a stock ticker.

    Internally fetches 260 daily candles from FMP, computes all indicators on
    1D and 1W timeframes, and returns pre-calculated analysis. The `limit`
    parameter controls how many raw OHLCV candles are exposed (default 5).

    Args:
        ticker: Stock ticker symbol (e.g. AAPL, MSFT).
        limit: Number of raw OHLCV candles to include in ohlcv_1d/1w (default 5).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        dict: Enriched TA payload with keys: meta, ohlcv_1d, ohlcv_1w,
              momentum_1d, trend_1d, trend_1w, volatility_1d, volume_1d,
              price_structure, ics_1d, ics_1w, patterns_1d, patterns_1w, alpha101.
              Returns {"error": str, "symbol": ticker, "raw_ohlcv": list} on failure.
    """
    import os
    import numpy as np
    import requests
    from scheduler.tools import _ta

    api_key = api_key or os.environ["FMP_API_KEY"]
    params = {"symbol": ticker, "limit": 260, "apikey": api_key}
    response = requests.get(
        "https://financialmodelingprep.com/stable/historical-price-eod/full",
        params=params, timeout=30,
    )
    response.raise_for_status()
    raw = response.json()
    records = raw if isinstance(raw, list) else raw.get("historical", [])

    # FMP returns newest-first; sort oldest-first for all calculations
    records = sorted(records, key=lambda r: r["date"])

    def _rows(recs, n):
        return [{"d": r["date"], "o": round(float(r["open"]), 2),
                 "h": round(float(r["high"]), 2), "l": round(float(r["low"]), 2),
                 "c": round(float(r["close"]), 2), "v": int(r["volume"])}
                for r in recs[-n:]]

    def _wrows(ws, n):
        return [{"d": w["d"], "o": round(w["o"], 2), "h": round(w["h"], 2),
                 "l": round(w["l"], 2), "c": round(w["c"], 2), "v": int(w["v"])}
                for w in ws[-n:]]

    raw_5 = _rows(records, 5)

    # Hard-reject below 30: not enough for even short-period indicators.
    # Between 30-199 candles: TA-Lib returns NaN/None for long-period indicators
    # (e.g., 90-day RSI stats) — graceful degradation per spec.
    # 200+ candles gives full indicator coverage (spec quality threshold).
    if len(records) < 30:
        return {"symbol": ticker, "error": "insufficient_data",
                "candles_received": len(records),
                "raw_ohlcv": raw_5}

    dates  = [r["date"]   for r in records]
    open_  = np.array([r["open"]   for r in records], dtype=float)
    high   = np.array([r["high"]   for r in records], dtype=float)
    low    = np.array([r["low"]    for r in records], dtype=float)
    close  = np.array([r["close"]  for r in records], dtype=float)
    volume = np.array([r["volume"] for r in records], dtype=float)

    weekly  = _ta.resample_weekly(records)
    # Use only complete weeks for indicator calculations (exclude current partial week)
    complete_weekly = weekly[:-1] if len(weekly) > 1 else weekly
    w_dates  = [w["d"] for w in complete_weekly]
    w_open   = np.array([w["o"] for w in complete_weekly], dtype=float)
    w_high   = np.array([w["h"] for w in complete_weekly], dtype=float)
    w_low    = np.array([w["l"] for w in complete_weekly], dtype=float)
    w_close  = np.array([w["c"] for w in complete_weekly], dtype=float)
    w_volume = np.array([w["v"] for w in complete_weekly], dtype=float)

    try:
        vwap_result = _ta.calc_vwap(high, low, close, volume, dates)
        vwap_series = vwap_result["vwap_series"]

        return {
            "meta": {
                "symbol": ticker,
                "as_of": dates[-1],
                "price": round(float(close[-1]), 2),
            },
            "ohlcv_1d": _rows(records, limit),
            "ohlcv_1w": _wrows(weekly, limit),  # includes current partial week for display
            "momentum_1d": {
                **_ta.calc_rsi(close),
                **_ta.calc_macd(close),
                **_ta.calc_stoch(high, low, close),
                "mfi": _ta.calc_mfi(high, low, close, volume),
            },
            "trend_1d": {
                **_ta.calc_ema_samples(close, dates, periods=[21, 55, 89]),
                **_ta.calc_adx(high, low, close, timeframe="1d"),
                **{k: v for k, v in vwap_result.items() if k != "vwap_series"},
            },
            "trend_1w": {
                **_ta.calc_ema_samples(w_close, w_dates, periods=[21, 55]),
                **_ta.calc_adx(w_high, w_low, w_close, timeframe="1w"),
            },
            "volatility_1d": {
                **_ta.calc_atr(high, low, close),
                **_ta.calc_bollinger(close),
            },
            "volume_1d": {
                **_ta.calc_volume_ratio(volume, w_volume),
                **_ta.calc_obv(close, volume),
            },
            "price_structure": {
                "sr_1d": _ta.calc_support_resistance(
                    high, low, close, volume, dates, n_support=3, n_resist=3),
                "sr_1w": _ta.calc_support_resistance(
                    w_high, w_low, w_close, w_volume, w_dates, n_support=2, n_resist=2),
                "pivot_1d": _ta.calc_pivot_points(high, low, close),
                "wk52": _ta.calc_52w_range(close),
            },
            "ics_1d": _ta.calc_ics(open_, high, low, close, volume, dates, timeframe="1d"),
            "ics_1w": _ta.calc_ics(w_open, w_high, w_low, w_close, w_volume, w_dates, timeframe="1w"),
            "patterns_1d": _ta.calc_patterns(open_, high, low, close, dates, lookback=5),
            "patterns_1w": _ta.calc_patterns(w_open, w_high, w_low, w_close, w_dates, lookback=3),
            "alpha101": _ta.calc_alpha101(open_, high, low, close, volume, vwap_series),
        }
    except Exception as exc:
        return {"error": str(exc), "symbol": ticker, "raw_ohlcv": raw_5}


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
