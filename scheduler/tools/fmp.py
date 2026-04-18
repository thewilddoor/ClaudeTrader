# scheduler/tools/fmp.py
"""Financial Modeling Prep (FMP) market data tools."""
from typing import Optional


def fmp_screener(
    market_cap_more_than: int = 2_000_000_000,
    market_cap_less_than: Optional[int] = None,
    volume_more_than: int = 1_000_000,
    volume_less_than: Optional[int] = None,
    price_more_than: Optional[float] = 15.0,
    price_less_than: Optional[float] = None,
    beta_more_than: Optional[float] = None,
    beta_less_than: Optional[float] = None,
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    country: str = "US",
    dividend_more_than: Optional[float] = None,
    dividend_less_than: Optional[float] = None,
    exchange: str = "NYSE,NASDAQ",
    is_actively_trading: bool = True,
    is_etf: bool = False,
    limit: int = 20,
    api_key: Optional[str] = None,
) -> list:
    """Screen US stocks with full filter support. Use directly for all strategy types
    including momentum, defensive, earnings catalyst, and short candidates.

    Args:
        market_cap_more_than: Minimum market cap in USD (default 2 billion).
        market_cap_less_than: Maximum market cap in USD (optional).
        volume_more_than: Minimum average daily volume (default 1 million).
        volume_less_than: Maximum average daily volume (optional).
        price_more_than: Minimum stock price in USD (default $15 — excludes micro-cap noise).
        price_less_than: Maximum stock price in USD (optional).
        beta_more_than: Minimum beta — use >1.0 for momentum, >1.5 for aggressive (optional).
        beta_less_than: Maximum beta — use <1.0 for defensive/quality (optional).
        sector: Sector filter (optional). Valid values: Technology, Healthcare,
            Consumer Cyclical, Consumer Defensive, Financial Services, Industrials,
            Energy, Basic Materials, Communication Services, Real Estate, Utilities.
        industry: Industry sub-filter within sector (optional).
        country: Country filter (default US).
        dividend_more_than: Minimum dividend yield — use for income/defensive screens (optional).
        dividend_less_than: Maximum dividend yield — use to exclude REITs/utilities (optional).
        exchange: Comma-separated exchanges (default NYSE,NASDAQ).
        is_actively_trading: Only include actively traded stocks (default True).
        is_etf: Include ETFs (default False — exclude ETFs).
        limit: Maximum results (default 20 — keep small; follow with fmp_ta on top candidates).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        list: Matching stock records with symbol, price, volume, marketCap, beta, sector fields.
    """
    import os
    import requests

    api_key = api_key or os.environ["FMP_API_KEY"]
    params: dict = {
        "marketCapMoreThan": market_cap_more_than,
        "volumeMoreThan": volume_more_than,
        "exchange": exchange,
        "isActivelyTrading": str(is_actively_trading).lower(),
        "isEtf": str(is_etf).lower(),
        "country": country,
        "limit": limit,
        "apikey": api_key,
    }
    if market_cap_less_than is not None:
        params["marketCapLowerThan"] = market_cap_less_than
    if volume_less_than is not None:
        params["volumeLowerThan"] = volume_less_than
    if price_more_than is not None:
        params["priceMoreThan"] = price_more_than
    if price_less_than is not None:
        params["priceLowerThan"] = price_less_than
    if beta_more_than is not None:
        params["betaMoreThan"] = beta_more_than
    if beta_less_than is not None:
        params["betaLowerThan"] = beta_less_than
    if sector is not None:
        params["sector"] = sector
    if industry is not None:
        params["industry"] = industry
    if dividend_more_than is not None:
        params["dividendMoreThan"] = dividend_more_than
    if dividend_less_than is not None:
        params["dividendLowerThan"] = dividend_less_than

    response = requests.get(
        "https://financialmodelingprep.com/stable/company-screener",
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fmp_ta(ticker: str, limit: int = 5, api_key: Optional[str] = None) -> dict:
    """Get a professional technical analysis payload for a stock ticker.

    Internally fetches 260 daily candles from FMP, computes all indicators on
    1D and 1W timeframes, and returns pre-calculated analysis. The `limit`
    parameter controls how many raw OHLCV candles are exposed (default 5).

    Use fmp_check_current_price when you only need the live price (e.g. at
    market_open to verify entry zone before placing an order).

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


def fmp_check_current_price(ticker: str, api_key: Optional[str] = None) -> dict:
    """Get live price and intraday snapshot for a ticker. Lightweight alternative
    to fmp_ta when only current price is needed (e.g. verifying entry zone at
    market_open before placing an order).

    Args:
        ticker: Stock ticker symbol (e.g. AAPL, MSFT).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        dict: {symbol, price, open, day_high, day_low, prev_close,
               change_pct, volume, avg_volume, vol_ratio}
    """
    import os
    import requests

    api_key = api_key or os.environ["FMP_API_KEY"]
    params = {"symbol": ticker, "apikey": api_key}
    response = requests.get(
        "https://financialmodelingprep.com/stable/quote",
        params=params,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    q = data[0] if isinstance(data, list) and data else data

    price = float(q.get("price", 0))
    prev_close = float(q.get("previousClose", 0))
    change_pct = round((price - prev_close) / prev_close, 4) if prev_close else None
    avg_volume = q.get("avgVolume") or 0
    volume = q.get("volume") or 0
    vol_ratio = round(volume / avg_volume, 2) if avg_volume else None

    return {
        "symbol": q.get("symbol", ticker),
        "price": round(price, 2),
        "open": round(float(q.get("open", 0)), 2),
        "day_high": round(float(q.get("dayHigh", 0)), 2),
        "day_low": round(float(q.get("dayLow", 0)), 2),
        "prev_close": round(prev_close, 2),
        "change_pct": change_pct,
        "volume": int(volume),
        "avg_volume": int(avg_volume),
        "vol_ratio": vol_ratio,
    }


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
