import json
import responses
import pytest
from datetime import date, timedelta
from scheduler.tools.fmp import (
    fmp_screener,
    fmp_ta,
    fmp_check_current_price,
    fmp_news,
    fmp_earnings_calendar,
)

_SCREENER_URL = "https://financialmodelingprep.com/stable/company-screener"
_SAMPLE_STOCK = {"symbol": "AAPL", "marketCap": 3_000_000_000_000, "volume": 60_000_000, "beta": 1.3, "sector": "Technology"}
_BULK_SURPRISES_URL = "https://financialmodelingprep.com/stable/earnings-surprises-bulk"
_QUOTE_URL = "https://financialmodelingprep.com/stable/quote"


def _recent_trading_date() -> str:
    """Return the most recent weekday (Mon–Fri) as ISO string."""
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


@responses.activate
def test_fmp_screener_returns_list():
    responses.add(responses.GET, _SCREENER_URL, json=[_SAMPLE_STOCK], status=200)
    result = fmp_screener(market_cap_more_than=1_000_000_000, volume_more_than=500_000,
                          pead=False, api_key="test")
    assert isinstance(result, list)
    assert result[0]["symbol"] == "AAPL"
    assert result[0]["pead_candidate"] is False


@responses.activate
def test_fmp_screener_sends_new_params():
    """Expanded screener sends beta, sector, price params."""
    responses.add(responses.GET, _SCREENER_URL, json=[_SAMPLE_STOCK], status=200)
    fmp_screener(
        market_cap_more_than=2_000_000_000,
        price_more_than=15.0,
        beta_more_than=1.0,
        beta_less_than=2.5,
        sector="Technology",
        limit=20,
        pead=False,
        api_key="test",
    )
    sent_params = responses.calls[0].request.url
    assert "betaMoreThan=1.0" in sent_params or "betaMoreThan=1" in sent_params
    assert "betaLowerThan=2.5" in sent_params
    assert "sector=Technology" in sent_params
    assert "priceMoreThan=15.0" in sent_params or "priceMoreThan=15" in sent_params


@responses.activate
def test_fmp_screener_omits_none_params():
    """Optional params set to None must not appear in the request URL."""
    responses.add(responses.GET, _SCREENER_URL, json=[], status=200)
    fmp_screener(beta_more_than=None, sector=None, dividend_more_than=None,
                 pead=False, api_key="test")
    sent_params = responses.calls[0].request.url
    assert "betaMoreThan" not in sent_params
    assert "sector" not in sent_params
    assert "dividendMoreThan" not in sent_params


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


@responses.activate
def test_fmp_screener_pead_off_makes_one_call():
    """pead=False makes exactly 1 HTTP call — no earnings or quote calls."""
    responses.add(responses.GET, _SCREENER_URL, json=[_SAMPLE_STOCK], status=200)
    result = fmp_screener(pead=False, api_key="test")
    assert len(responses.calls) == 1
    assert result[0]["pead_candidate"] is False


@responses.activate
def test_fmp_screener_pead_on_merges_candidates():
    """pead=True merges earnings surprise candidates into results with correct fields."""
    earnings_date = _recent_trading_date()

    responses.add(responses.GET, _SCREENER_URL,
                  json=[{"symbol": "MSFT", "marketCap": 3_000_000_000_000,
                         "volume": 5_000_000, "sector": "Technology"}],
                  status=200)
    responses.add(responses.GET, _BULK_SURPRISES_URL,
                  json=[{"symbol": "NVDA", "date": earnings_date,
                         "actualEarningResult": 5.16, "estimatedEarning": 3.84}],
                  status=200)
    responses.add(responses.GET, _QUOTE_URL,
                  json=[{"symbol": "NVDA", "price": 892.0,
                         "marketCap": 2_200_000_000_000, "avgVolume": 3_400_000,
                         "volume": 4_000_000, "sector": "Technology"}],
                  status=200)

    result = fmp_screener(pead=True, api_key="test")

    assert len(responses.calls) == 3
    symbols = {r["symbol"] for r in result}
    assert "MSFT" in symbols
    assert "NVDA" in symbols

    msft = next(r for r in result if r["symbol"] == "MSFT")
    assert msft["pead_candidate"] is False
    assert "eps_surprise_pct" not in msft

    nvda = next(r for r in result if r["symbol"] == "NVDA")
    assert nvda["pead_candidate"] is True
    assert nvda["eps_surprise_pct"] == pytest.approx(34.375, rel=0.01)
    assert nvda["eps_actual"] == 5.16
    assert nvda["eps_estimated"] == 3.84
    assert nvda["earnings_date"] == earnings_date


@responses.activate
def test_fmp_screener_pead_deduplicates_on_symbol():
    """When a ticker appears in both screener and PEAD results, PEAD version wins."""
    earnings_date = _recent_trading_date()

    responses.add(responses.GET, _SCREENER_URL,
                  json=[{"symbol": "AAPL", "marketCap": 3_000_000_000_000,
                         "volume": 60_000_000, "sector": "Technology"}],
                  status=200)
    responses.add(responses.GET, _BULK_SURPRISES_URL,
                  json=[{"symbol": "AAPL", "date": earnings_date,
                         "actualEarningResult": 2.18, "estimatedEarning": 1.70}],
                  status=200)
    responses.add(responses.GET, _QUOTE_URL,
                  json=[{"symbol": "AAPL", "price": 185.0,
                         "marketCap": 3_000_000_000_000, "avgVolume": 50_000_000,
                         "volume": 60_000_000, "sector": "Technology"}],
                  status=200)

    result = fmp_screener(pead=True, api_key="test")

    aapl_entries = [r for r in result if r["symbol"] == "AAPL"]
    assert len(aapl_entries) == 1, "Duplicate AAPL entries found"
    assert aapl_entries[0]["pead_candidate"] is True


@responses.activate
def test_fmp_screener_pead_year_boundary_calls_two_bulk_years():
    """In January, PEAD sub-flow calls earnings-surprises-bulk for both current and prior year."""
    from unittest.mock import patch

    responses.add(responses.GET, _SCREENER_URL, json=[], status=200)
    responses.add(responses.GET, _BULK_SURPRISES_URL, json=[], status=200)
    responses.add(responses.GET, _BULK_SURPRISES_URL, json=[], status=200)

    with patch("scheduler.tools.fmp._get_today", return_value=date(2026, 1, 3)):
        fmp_screener(pead=True, api_key="test")

    bulk_calls = [c for c in responses.calls if "earnings-surprises-bulk" in c.request.url]
    assert len(bulk_calls) == 2
    years = {c.request.url.split("year=")[1].split("&")[0] for c in bulk_calls}
    assert years == {"2026", "2025"}
