# scheduler/tools/fmp.py
"""Financial Modeling Prep (FMP) market data tools."""
import json
import logging
from typing import Optional
from datetime import date, timedelta

log = logging.getLogger(__name__)


def _parse_json_lenient(text: str):
    """Parse JSON from response text, tolerating extra data after the first value.

    FMP occasionally returns a response with trailing content (e.g. a newline
    followed by a second JSON object). Python's json.loads() rejects this with
    'Extra data'. Using JSONDecoder.raw_decode() accepts the first complete JSON
    value and ignores the rest.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        result, _ = json.JSONDecoder().raw_decode(text.strip())
        return result


def _get_today() -> date:
    """Returns today's date. Module-level for testability (patchable in tests)."""
    return date.today()


def _last_n_trading_days(n: int, today: date) -> set:
    """Return a set of ISO date strings for the last n Mon–Fri days including today."""
    dates: set = set()
    d = today
    while len(dates) < n:
        if d.weekday() < 5:
            dates.add(d.isoformat())
        d -= timedelta(days=1)
    return dates


def _fetch_pead_candidates(
    api_key: str,
    market_cap_more_than: int,
    volume_more_than: int,
    sector: Optional[str],
    pead_min_surprise_pct: float,
    pead_lookback_days: int,
) -> list:
    """Fetch PEAD candidates via FMP bulk earnings surprises + quote batch.

    Returns list of dicts with pead_candidate=True and earnings fields.
    Applies market_cap_more_than, volume_more_than, and sector filters.
    """
    import requests

    today = _get_today()
    trading_dates = _last_n_trading_days(pead_lookback_days, today)

    years = [today.year]
    if today.month == 1:
        years.append(today.year - 1)

    raw_surprises: list = []
    for year in years:
        resp = requests.get(
            "https://financialmodelingprep.com/stable/earnings-surprises-bulk",
            params={"year": year, "apikey": api_key},
            timeout=30,
        )
        resp.raise_for_status()
        data = _parse_json_lenient(resp.text)
        if isinstance(data, list):
            raw_surprises.extend(data)

    qualified: list = []
    for item in raw_surprises:
        earnings_date = item.get("date", "")
        if earnings_date not in trading_dates:
            continue
        eps_estimated = item.get("estimatedEarning") or item.get("epsEstimated")
        eps_actual = item.get("actualEarningResult") or item.get("epsActual")
        if eps_estimated is None or eps_actual is None:
            continue
        if abs(float(eps_estimated)) <= 0.01:
            continue
        surprise_pct = (
            (float(eps_actual) - float(eps_estimated)) / abs(float(eps_estimated)) * 100
        )
        if surprise_pct < pead_min_surprise_pct:
            continue
        qualified.append({
            "symbol": item["symbol"],
            "eps_actual": round(float(eps_actual), 4),
            "eps_estimated": round(float(eps_estimated), 4),
            "eps_surprise_pct": round(surprise_pct, 2),
            "earnings_date": earnings_date,
        })

    if not qualified:
        return []

    qualified.sort(key=lambda x: x["eps_surprise_pct"], reverse=True)
    qualified = qualified[:20]

    symbols_str = ",".join(q["symbol"] for q in qualified)
    resp = requests.get(
        "https://financialmodelingprep.com/stable/quote",
        params={"symbol": symbols_str, "apikey": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    raw_quotes = _parse_json_lenient(resp.text)
    quote_map: dict = {}
    if isinstance(raw_quotes, list):
        quote_map = {q["symbol"]: q for q in raw_quotes}

    candidates: list = []
    for item in qualified:
        sym = item["symbol"]
        q = quote_map.get(sym, {})
        mkt_cap = float(q.get("marketCap") or 0)
        avg_vol = float(q.get("avgVolume") or 0)
        sym_sector = q.get("sector") or ""

        if mkt_cap < market_cap_more_than:
            continue
        if avg_vol < volume_more_than:
            continue
        if sector is not None and sym_sector != sector:
            continue

        candidates.append({
            "symbol": sym,
            "price": round(float(q.get("price") or 0), 2),
            "marketCap": int(mkt_cap),
            "volume": int(q.get("volume") or 0),
            "sector": sym_sector,
            "pead_candidate": True,
            "eps_surprise_pct": item["eps_surprise_pct"],
            "eps_actual": item["eps_actual"],
            "eps_estimated": item["eps_estimated"],
            "earnings_date": item["earnings_date"],
        })

    return candidates


def fmp_screener(
    market_cap_more_than: int = 2_000_000_000,
    volume_more_than: int = 1_000_000,
    market_cap_less_than: Optional[int] = None,
    volume_less_than: Optional[int] = None,
    price_more_than: Optional[float] = None,
    price_less_than: Optional[float] = None,
    beta_more_than: Optional[float] = None,
    beta_less_than: Optional[float] = None,
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    dividend_more_than: Optional[float] = None,
    dividend_less_than: Optional[float] = None,
    limit: int = 30,
    pead: bool = True,
    pead_min_surprise_pct: float = 21.9,
    pead_lookback_days: int = 5,
    api_key: Optional[str] = None,
) -> list:
    """Screen US stocks with full filter support. PEAD candidates are automatically
    appended when pead=True (default).

    Args:
        market_cap_more_than: Minimum market cap in USD (default 2 billion).
        volume_more_than: Minimum average daily volume (default 1 million).
        market_cap_less_than: Maximum market cap in USD (optional).
        volume_less_than: Maximum average daily volume (optional).
        price_more_than: Minimum stock price in USD (optional).
        price_less_than: Maximum stock price in USD (optional).
        beta_more_than: Minimum beta — use >1.0 for momentum, >1.5 for shorts (optional).
        beta_less_than: Maximum beta — use <1.0 for defensive (optional).
        sector: Sector filter (optional). Valid values: Technology, Healthcare,
            Consumer Cyclical, Consumer Defensive, Financial Services, Industrials,
            Energy, Basic Materials, Communication Services, Real Estate, Utilities.
        industry: Industry sub-filter within sector (optional).
        dividend_more_than: Minimum dividend yield (optional).
        dividend_less_than: Maximum dividend yield — set 0.5 to exclude high-yield names (optional).
        limit: Maximum screener results (default 30).
        pead: When True, appends post-earnings drift candidates to results (default True).
            PEAD candidates have pead_candidate=True plus eps_surprise_pct, eps_actual,
            eps_estimated, earnings_date fields.
        pead_min_surprise_pct: Minimum EPS surprise % to qualify as PEAD candidate (default 21.9).
        pead_lookback_days: Trading days to look back for earnings reports (default 5).
        api_key: FMP API key; reads from FMP_API_KEY env var if not provided.

    Returns:
        list: Stock records. All items have pead_candidate: bool.
              PEAD items additionally have eps_surprise_pct, eps_actual, eps_estimated, earnings_date.
    """
    import os
    import requests

    api_key = api_key or os.environ["FMP_API_KEY"]

    params: dict = {
        "marketCapMoreThan": market_cap_more_than,
        "volumeMoreThan": volume_more_than,
        "exchange": "NYSE,NASDAQ",
        "isActivelyTrading": "true",
        "isEtf": "false",
        "country": "US",
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
    raw = _parse_json_lenient(response.text)
    results = [{**r, "pead_candidate": False} for r in raw] if isinstance(raw, list) else []

    if not pead:
        return results

    try:
        pead_candidates = _fetch_pead_candidates(
            api_key=api_key,
            market_cap_more_than=market_cap_more_than,
            volume_more_than=volume_more_than,
            sector=sector,
            pead_min_surprise_pct=pead_min_surprise_pct,
            pead_lookback_days=pead_lookback_days,
        )
    except Exception as exc:
        log.warning("PEAD fetch failed (returning screener results only): %s", exc)
        pead_candidates = []

    if pead_candidates:
        pead_symbols = {p["symbol"] for p in pead_candidates}
        results = [r for r in results if r["symbol"] not in pead_symbols]
        results.extend(pead_candidates)

    return results


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
