# Unified Screener + PEAD Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 4 preset screener functions with a single `fmp_screener` that has only market_cap and volume defaults, exposes all other filters as optional params, and includes a `pead=True` toggle that auto-appends post-earnings drift candidates to every screener result.

**Architecture:** `fmp_screener` calls the FMP company-screener, then when `pead=True` makes two additional calls (bulk earnings surprises + batch quote) to find stocks that reported earnings in the last 5 trading days with EPS surprise ≥ 21.9%, merges them into the result list tagged with `pead_candidate=True`. Private helper functions handle the PEAD sub-flow and are not registered as tools.

**Tech Stack:** Python, `responses` library (HTTP mocking in tests), FMP REST API (`/stable/company-screener`, `/stable/earnings-surprises-bulk`, `/stable/quote`)

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `scheduler/tools/fmp.py` | Modify | Remove 4 preset functions; rewrite `fmp_screener`; add `_get_today`, `_last_n_trading_days`, `_fetch_pead_candidates` helpers |
| `scheduler/tools/registry.py` | Modify | Remove 4 preset imports from `ALL_TOOLS` |
| `scheduler/agent.py` | Modify | Replace regime→screener table with unified screener guidance; add PEAD candidate evaluation section; remove preset references from Tool Reference |
| `tests/test_tools/test_fmp.py` | Modify | Remove 8 preset tests; update 3 existing screener tests to pass `pead=False`; add 4 new PEAD tests |
| `tests/test_tool_defaults.py` | Modify | Replace `test_fmp_screener_presets_exist` with `test_fmp_screener_has_pead_params`; update `test_fmp_screener_has_expanded_params` |

---

## Task 1: Remove preset functions from fmp.py and registry.py

**Files:**
- Modify: `scheduler/tools/fmp.py`
- Modify: `scheduler/tools/registry.py`

- [ ] **Step 1: Delete the 4 preset functions from fmp.py**

In `scheduler/tools/fmp.py`, delete everything from line 100 (`def fmp_screen_momentum(`) through line 325 (end of `fmp_screen_short_candidates`). The file should jump directly from `fmp_screener` to `fmp_ta`.

- [ ] **Step 2: Update registry.py imports**

Replace the import block in `scheduler/tools/registry.py`:

```python
# OLD — replace this entire block:
from scheduler.tools.fmp import (
    fmp_screener,
    fmp_screen_momentum,
    fmp_screen_earnings_catalyst,
    fmp_screen_quality_defensive,
    fmp_screen_short_candidates,
    fmp_ta,
    fmp_check_current_price,
    fmp_news,
    fmp_earnings_calendar,
)
```

```python
# NEW:
from scheduler.tools.fmp import (
    fmp_screener,
    fmp_ta,
    fmp_check_current_price,
    fmp_news,
    fmp_earnings_calendar,
)
```

- [ ] **Step 3: Update ALL_TOOLS in registry.py**

```python
# OLD:
ALL_TOOLS = [
    fmp_screener,
    fmp_screen_momentum,
    fmp_screen_earnings_catalyst,
    fmp_screen_quality_defensive,
    fmp_screen_short_candidates,
    fmp_ta,
    ...
]
```

```python
# NEW:
ALL_TOOLS = [
    fmp_screener,
    fmp_ta,
    fmp_check_current_price,
    fmp_news,
    fmp_earnings_calendar,
    serper_search,
    run_script,
    alpaca_get_account,
    alpaca_get_positions,
    alpaca_place_order,
    alpaca_list_orders,
    alpaca_cancel_order,
    trade_open,
    trade_close,
    hypothesis_log,
    trade_query,
]
```

- [ ] **Step 4: Run tests to see what breaks**

```bash
pytest --tb=short -q
```

Expected: failures in `test_fmp.py` (8 preset tests) and `test_tool_defaults.py` (2 tests). Everything else should pass. Note the exact failure count — it tells you nothing unintended broke.

- [ ] **Step 5: Remove preset tests from test_fmp.py**

In `tests/test_tools/test_fmp.py`, delete the following 8 test functions and update the import at the top:

Functions to delete (lines ~64–156):
- `test_fmp_screen_momentum_returns_list`
- `test_fmp_screen_momentum_sends_correct_params`
- `test_fmp_screen_earnings_catalyst_returns_list`
- `test_fmp_screen_earnings_catalyst_no_beta_filter`
- `test_fmp_screen_quality_defensive_returns_list`
- `test_fmp_screen_quality_defensive_sends_correct_params`
- `test_fmp_screen_short_candidates_returns_list`
- `test_fmp_screen_short_candidates_sends_correct_params`

Update the import at the top of `test_fmp.py`:

```python
# OLD:
from scheduler.tools.fmp import (
    fmp_screener,
    fmp_screen_momentum,
    fmp_screen_earnings_catalyst,
    fmp_screen_quality_defensive,
    fmp_screen_short_candidates,
    fmp_ta,
    fmp_check_current_price,
    fmp_news,
    fmp_earnings_calendar,
)
```

```python
# NEW:
from scheduler.tools.fmp import (
    fmp_screener,
    fmp_ta,
    fmp_check_current_price,
    fmp_news,
    fmp_earnings_calendar,
)
```

- [ ] **Step 6: Fix test_tool_defaults.py**

In `tests/test_tool_defaults.py`, replace the two affected tests:

```python
# DELETE this entire function:
def test_fmp_screener_presets_exist():
    """All four named screener presets must be defined in fmp.py."""
    src = pathlib.Path("scheduler/tools/fmp.py").read_text()
    tree = ast.parse(src)
    fn_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    expected = {
        "fmp_screen_momentum",
        "fmp_screen_earnings_catalyst",
        "fmp_screen_quality_defensive",
        "fmp_screen_short_candidates",
    }
    missing = expected - fn_names
    assert not missing, f"Missing screener presets: {missing}"
```

```python
# REPLACE test_fmp_screener_has_expanded_params with this:
def test_fmp_screener_has_expanded_params():
    """fmp_screener should expose the full filter set including beta, sector, price, and PEAD."""
    src = pathlib.Path("scheduler/tools/fmp.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fmp_screener":
            arg_names = {a.arg for a in node.args.args}
            required_params = {
                "beta_more_than", "beta_less_than",
                "sector", "price_more_than",
                "pead", "pead_min_surprise_pct", "pead_lookback_days",
            }
            missing = required_params - arg_names
            assert not missing, f"fmp_screener missing params: {missing}"
            return
    raise AssertionError("fmp_screener not found")
```

- [ ] **Step 7: Run tests — should be mostly green now**

```bash
pytest --tb=short -q
```

Expected: remaining failures only in `test_fmp_screener_returns_list` and `test_fmp_screener_sends_new_params` and `test_fmp_screener_omits_none_params` (they call `fmp_screener` without `pead=False`, so when we add PEAD they'll make unexpected HTTP calls). These get fixed in Task 2. Everything else should pass.

- [ ] **Step 8: Commit**

```bash
git add scheduler/tools/fmp.py scheduler/tools/registry.py \
        tests/test_tools/test_fmp.py tests/test_tool_defaults.py
git commit -m "refactor: remove 4 preset screeners, update tests and registry"
```

---

## Task 2: Write failing PEAD tests

**Files:**
- Modify: `tests/test_tools/test_fmp.py`

- [ ] **Step 1: Add URL constants and helper at top of test_fmp.py**

After the existing `_SCREENER_URL` and `_SAMPLE_STOCK` constants, add:

```python
_BULK_SURPRISES_URL = "https://financialmodelingprep.com/stable/earnings-surprises-bulk"
_QUOTE_URL = "https://financialmodelingprep.com/stable/quote"

def _recent_trading_date() -> str:
    """Return the most recent weekday (Mon–Fri) as ISO string."""
    d = date.today()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()
```

- [ ] **Step 2: Update 3 existing screener tests to disable PEAD**

The 3 existing screener tests only mock one URL (`_SCREENER_URL`). Once `pead=True` is the default they'll make additional unmocked calls. Add `pead=False` to each:

```python
@responses.activate
def test_fmp_screener_returns_list():
    responses.add(responses.GET, _SCREENER_URL, json=[_SAMPLE_STOCK], status=200)
    result = fmp_screener(market_cap_more_than=1_000_000_000, volume_more_than=500_000,
                          pead=False, api_key="test")
    assert isinstance(result, list)
    assert result[0]["symbol"] == "AAPL"
    assert result[0]["pead_candidate"] is False  # non-PEAD results always tagged False


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
```

- [ ] **Step 3: Add 4 new PEAD tests at the end of test_fmp.py**

```python
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

    # 3 registered responses: screener, bulk surprises, quote batch
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
    # Two bulk calls — one for each year
    responses.add(responses.GET, _BULK_SURPRISES_URL, json=[], status=200)
    responses.add(responses.GET, _BULK_SURPRISES_URL, json=[], status=200)

    with patch("scheduler.tools.fmp._get_today", return_value=date(2026, 1, 3)):
        fmp_screener(pead=True, api_key="test")

    bulk_calls = [c for c in responses.calls if "earnings-surprises-bulk" in c.request.url]
    assert len(bulk_calls) == 2
    years = {c.request.url.split("year=")[1].split("&")[0] for c in bulk_calls}
    assert years == {"2026", "2025"}
```

- [ ] **Step 4: Run new tests — they should all FAIL**

```bash
pytest tests/test_tools/test_fmp.py -k "pead" -v
```

Expected: 4 failures — `fmp_screener` does not yet have PEAD logic. If any pass unexpectedly, the test is wrong — fix before continuing.

- [ ] **Step 5: Commit failing tests**

```bash
git add tests/test_tools/test_fmp.py
git commit -m "test: add failing PEAD screener tests"
```

---

## Task 3: Implement unified fmp_screener with PEAD sub-flow

**Files:**
- Modify: `scheduler/tools/fmp.py`

- [ ] **Step 1: Add module-level date imports and helper functions at top of fmp.py**

After the existing `from typing import Optional` line, add:

```python
from datetime import date, timedelta


def _get_today() -> date:
    """Returns today's date. Module-level for testability (can be patched in tests)."""
    return date.today()


def _last_n_trading_days(n: int, today: date) -> set:
    """Return a set of ISO date strings for the last n Mon–Fri days up to and including today."""
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
        data = resp.json()
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
    raw_quotes = resp.json()
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
```

- [ ] **Step 2: Rewrite fmp_screener**

Replace the entire existing `fmp_screener` function (everything from `def fmp_screener(` through the closing `return response.json()`) with:

```python
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
            PEAD candidates have pead_candidate=True, eps_surprise_pct, eps_actual,
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
    results = [{**r, "pead_candidate": False} for r in response.json()]

    if not pead:
        return results

    pead_candidates = _fetch_pead_candidates(
        api_key=api_key,
        market_cap_more_than=market_cap_more_than,
        volume_more_than=volume_more_than,
        sector=sector,
        pead_min_surprise_pct=pead_min_surprise_pct,
        pead_lookback_days=pead_lookback_days,
    )

    if pead_candidates:
        pead_symbols = {p["symbol"] for p in pead_candidates}
        results = [r for r in results if r["symbol"] not in pead_symbols]
        results.extend(pead_candidates)

    return results
```

- [ ] **Step 3: Run PEAD tests — should now pass**

```bash
pytest tests/test_tools/test_fmp.py -k "pead" -v
```

Expected: 4 PASS. If any fail, fix the implementation before continuing.

- [ ] **Step 4: Run full test suite**

```bash
pytest --tb=short -q
```

Expected: all tests pass. If `test_fmp_screener_sends_new_params` fails because `is_actively_trading` is no longer a param — that's fine, the test was updated in Task 1 to remove that assertion.

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/fmp.py
git commit -m "feat: unified fmp_screener with PEAD toggle"
```

---

## Task 4: Update STATIC_PROMPT in agent.py

**Files:**
- Modify: `scheduler/agent.py`

- [ ] **Step 1: Replace the regime→screener selection block**

Find this exact text in `scheduler/agent.py`:

```
3. REGIME → SCREENER SELECTION (choose one primary + optional secondary):
   bull_quiet  (VIX <15, spy_vs_ema55 >0):
     → fmp_screen_momentum(sector=<top_sector>, limit=20) — run for top 2 leading sectors
     → Identify leading sectors: fmp_ta("XLK"), fmp_ta("XLY"), fmp_ta("XLF") — pick top 2 by
       price_vs_ema55 + volume_ratio. Use their FMP sector name in fmp_screen_momentum.
   bull_volatile (VIX 15-25, spy_vs_ema55 >0):
     → fmp_screen_momentum(beta_more_than=1.0, beta_less_than=2.0, limit=20)
     → Also: fmp_screen_earnings_catalyst(limit=30) + cross-reference fmp_earnings_calendar(today, today+5)
   bear_quiet  (VIX <20, spy_vs_ema55 <0):
     → fmp_screen_short_candidates(sector=<weakest_sector>, limit=20)
     → fmp_screen_quality_defensive(limit=15) — for any remaining long bias
   bear_volatile (VIX >25):
     → fmp_screen_quality_defensive(beta_less_than=0.8, limit=20) ONLY
     → Reduce all planned position sizes by 50%. Default to cash unless exceptional setup.
   choppy/unclear (spy_adx <15):
     → fmp_screen_momentum(limit=15) + fmp_screen_quality_defensive(limit=15)
     → Only enter trades with ADX >25 on the individual stock.
   Earnings season override (Jan/Apr/Jul/Oct, weeks 2-4):
     → ALWAYS also run fmp_screen_earnings_catalyst + fmp_earnings_calendar(today, today+3)
     → PEAD setups (gap >3% on earnings day, volume >2x avg) are tier-1 priority in any regime.
```

Replace with:

```
3. SCREENER — call fmp_screener() with params matching the regime.
   Default call fmp_screener() is always valid — returns broad universe + PEAD candidates.
   Identify leading/lagging sectors first: fmp_ta("XLK"), fmp_ta("XLY"), fmp_ta("XLF"),
     fmp_ta("XLV") — pick top 2 by price_vs_ema55_pct + volume_ratio_1d.
     Use their FMP sector name in the sector= param.

   bull_quiet  (VIX <15, spy_vs_ema55 >0):
     → fmp_screener(beta_more_than=1.0, beta_less_than=2.8, sector=<top_sector>)
     → Run twice for top 2 leading sectors.
   bull_volatile (VIX 15-25, spy_vs_ema55 >0):
     → fmp_screener(beta_more_than=1.0, beta_less_than=2.0)
   bear_quiet  (VIX <20, spy_vs_ema55 <0):
     → fmp_screener(beta_more_than=1.5, sector=<weakest_sector>) — shorts universe
     → fmp_screener(beta_less_than=1.0, market_cap_more_than=5000000000) — defensive longs
   bear_volatile (VIX >25):
     → fmp_screener(beta_less_than=0.8, market_cap_more_than=5000000000) ONLY
     → Reduce all planned position sizes by 50%. Default to cash unless exceptional setup.
   choppy/unclear (spy_adx <15):
     → fmp_screener() — no optional params. Only enter if individual stock ADX >25.

   PEAD candidates (pead_candidate=True) appear automatically in all results.
   Earnings season (Jan/Apr/Jul/Oct, weeks 2–4): PEAD candidates are tier-1 priority.
```

- [ ] **Step 2: Replace the screener entries in the Tool Reference section**

Find this block in the `### Market Data` section:

```
fmp_screener(market_cap_more_than, market_cap_less_than, volume_more_than, volume_less_than,
             price_more_than, price_less_than, beta_more_than, beta_less_than, sector, industry,
             country, dividend_more_than, dividend_less_than, exchange, is_actively_trading,
             is_etf, limit)
  — Raw screener with full parameter set. Use named presets below for standard strategies.
  Valid sector values: Technology | Healthcare | Consumer Cyclical | Consumer Defensive |
    Financial Services | Industrials | Energy | Basic Materials | Communication Services |
    Real Estate | Utilities

fmp_screen_momentum(sector, beta_more_than=1.0, beta_less_than=2.8, market_cap_more_than=2B,
                    volume_more_than=1.5M, price_more_than=20, price_less_than, limit=20)
  — Bull regime: high-beta growth stocks in leading sectors. Run once per top sector.
  Excludes high-dividend names. Follow with fmp_ta on top 8-12 results.

fmp_screen_earnings_catalyst(sector, market_cap_more_than=1B, volume_more_than=800k,
                              price_more_than=15, price_less_than, limit=30)
  — Earnings season: broad universe for cross-ref with fmp_earnings_calendar.
  Cross-reference: fmp_earnings_calendar(today, today+3) to find next 3-day reporters.
  PEAD criteria: gap >3% on earnings day + volume >2x avg = tier-1 setup.

fmp_screen_quality_defensive(sector, beta_more_than=0.3, beta_less_than=1.0,
                              market_cap_more_than=5B, volume_more_than=1M,
                              dividend_more_than, limit=20)
  — Bear/volatile regime (VIX >25): quality large-caps, mean-reversion bounces.
  Prioritize: a12_capitulation >0, RSI_14 <35, price at major weekly support.

fmp_screen_short_candidates(sector, beta_more_than=1.5, beta_less_than, market_cap_more_than=2B,
                             volume_more_than=1M, price_more_than=20, limit=20)
  — Bear regime: high-beta names in weakening sectors to short.
  Confirm with fmp_ta: price <EMA21 <EMA55, ADX >20, a50_distribution < -0.5.
```

Replace with:

```
fmp_screener(market_cap_more_than=2B, volume_more_than=1M,
             [market_cap_less_than, volume_less_than, price_more_than, price_less_than,
              beta_more_than, beta_less_than, sector, industry,
              dividend_more_than, dividend_less_than, limit=30],
             pead=True, pead_min_surprise_pct=21.9, pead_lookback_days=5)
  — Unified screener. Default call fmp_screener() is always valid.
    Set params when you have a regime reason (see pre_market step 3).
  Valid sector values: Technology | Healthcare | Consumer Cyclical | Consumer Defensive |
    Financial Services | Industrials | Energy | Basic Materials | Communication Services |
    Real Estate | Utilities
  All results include pead_candidate: bool (False for standard screener results).
  PEAD results also include: eps_surprise_pct, eps_actual, eps_estimated, earnings_date.

  PEAD candidate evaluation (pead_candidate=True):
    Do NOT enter on earnings_date — gap day is too volatile.
    Entry via fmp_ta: price consolidating above gap, volume declining, ADX >20, price >EMA21.
    setup_type: always use "pead" in trade_open for PEAD-sourced trades.
    context_json: always include eps_surprise_pct and earnings_date.
    Exit discipline: close by earnings_date + 10 trading days OR stop hit — never hold open-ended.
    Skip if: VIX >80th percentile, earnings_date >8 trading days ago, initial gap >15%.
```

- [ ] **Step 3: Run tests**

```bash
pytest --tb=short -q
```

Expected: all pass. The `test_agent.py` schema coverage test checks tool names — confirm `fmp_screen_momentum` etc. no longer appear in TOOL_SCHEMAS. If the agent.py has a `TOOL_SCHEMAS` dict listing tool names, verify the 4 presets are removed from it.

- [ ] **Step 4: Commit**

```bash
git add scheduler/agent.py
git commit -m "feat: update STATIC_PROMPT with unified screener and PEAD candidate guidance"
```

---

## Task 5: Final verification

- [ ] **Step 1: Run full test suite with coverage**

```bash
pytest --tb=short -q
```

Expected output: all tests pass, no failures.

- [ ] **Step 2: Verify tool count in registry**

```bash
python -c "from scheduler.tools.registry import ALL_TOOLS; print([f.__name__ for f in ALL_TOOLS])"
```

Expected: 16 tools listed, none of the 4 old presets appear:
```
['fmp_screener', 'fmp_ta', 'fmp_check_current_price', 'fmp_news', 'fmp_earnings_calendar',
 'serper_search', 'run_script', 'alpaca_get_account', 'alpaca_get_positions',
 'alpaca_place_order', 'alpaca_list_orders', 'alpaca_cancel_order',
 'trade_open', 'trade_close', 'hypothesis_log', 'trade_query']
```

- [ ] **Step 3: Verify PEAD helper functions exist**

```bash
python -c "from scheduler.tools.fmp import _get_today, _last_n_trading_days, _fetch_pead_candidates; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: unified screener with PEAD integration complete"
```
