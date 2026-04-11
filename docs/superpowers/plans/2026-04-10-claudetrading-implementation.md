# ClaudeTrading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an autonomous AI trading system where a Letta-hosted Claude agent trades US stocks via Alpaca, learns from its own trade history, and evolves its strategy over time.

**Architecture:** APScheduler triggers five session types (pre-market, execution, health check, EOD reflection, weekly review), each sending a minimal prompt injection to a persistent Letta agent. The agent manages its own memory across sessions — strategy doc, trade journal, hypothesis ledger — using Letta's three-tier memory model. Tools (FMP, Serper, PyExec, Alpaca MCP) are registered with the Letta agent and called autonomously.

**Tech Stack:** Python 3.11, Letta (stateful agent platform), Alpaca MCP Server v2, APScheduler 3.x, python-telegram-bot 20.x, requests, pandas, pandas-ta, pytest, Docker Compose

---

## File Map

```
claudetrading/
  docker-compose.yml              # Letta + Alpaca MCP + scheduler services
  .env.example                    # all required env vars with comments
  .gitignore
  requirements.txt

  scheduler/
    __init__.py
    main.py                       # APScheduler: 5 triggers, session dispatch
    sessions.py                   # dynamic prompt injection builders per session type
    notifier.py                   # Telegram dispatcher (parses Claude's JSON output)
    bootstrap.py                  # one-time agent initialization
    agent.py                      # Letta client wrapper (connect, send, read memory)
    tools/
      __init__.py
      fmp.py                      # FMP HTTP tool (registered with Letta)
      serper.py                   # Serper HTTP tool (registered with Letta)
      pyexec.py                   # sandboxed subprocess execution tool
      registry.py                 # registers all tools with Letta agent

  scripts/
    indicators/
      index.json                  # script library index (Claude reads this)
      trend/
        ema_crossover.py
        adx_trend_strength.py
        supertrend.py
      momentum/
        rsi.py
        macd.py
        rate_of_change.py
      volatility/
        atr.py
        bollinger_bands.py
        vix_percentile.py
      volume/
        vwap.py
        obv.py
        volume_profile.py
      composite/
        market_regime_detector.py
        relative_strength_scanner.py
    analysis/                     # Claude-created scripts land here (starts empty)

  tests/
    conftest.py                   # shared fixtures
    test_agent.py
    test_sessions.py
    test_notifier.py
    test_tools/
      test_fmp.py
      test_serper.py
      test_pyexec.py
    test_indicators/
      test_rsi.py
      test_macd.py
      test_atr.py
      test_market_regime_detector.py

  logs/
    sessions/                     # per-session JSON logs
    errors/                       # error logs

  docs/
    superpowers/
      specs/
      plans/
```

---

## Phase 1: Project Scaffolding

### Task 1: Repository skeleton and dependencies

**Files:**
- Create: `requirements.txt`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `docker-compose.yml`

- [ ] **Step 1: Create requirements.txt**

```
letta>=0.6.0
apscheduler>=3.10.4
requests>=2.31.0
python-telegram-bot>=20.7
pandas>=2.1.0
pandas-ta>=0.3.14b
numpy>=1.26.0
pytest>=7.4.0
pytest-mock>=3.12.0
responses>=0.24.0
python-dotenv>=1.0.0
```

- [ ] **Step 2: Create .gitignore**

```
.env
__pycache__/
*.pyc
*.pyo
.pytest_cache/
logs/sessions/
logs/errors/
*.egg-info/
dist/
.DS_Store
letta-db/
```

- [ ] **Step 3: Create .env.example**

```bash
# Alpaca Paper Trading
ALPACA_API_KEY=your_alpaca_api_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_key_here
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Financial Modeling Prep
FMP_API_KEY=your_fmp_api_key_here

# Serper (Google Search)
SERPER_API_KEY=your_serper_api_key_here

# Telegram
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
TELEGRAM_CHAT_ID=your_telegram_chat_id_here

# Letta
LETTA_SERVER_URL=http://letta:8283
LETTA_AGENT_NAME=claude_trader

# Alpaca MCP server (internal Docker network URL)
ALPACA_MCP_URL=http://alpaca-mcp:8000/sse

# Scripts directory (mounted volume)
SCRIPTS_DIR=/app/scripts

# Claude model for Letta agent
ANTHROPIC_API_KEY=your_anthropic_api_key_here
```

- [ ] **Step 4: Create docker-compose.yml**

```yaml
version: "3.9"

services:
  letta:
    image: letta/letta:latest
    ports:
      - "8283:8283"
    volumes:
      - letta-db:/root/.letta
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    restart: unless-stopped

  alpaca-mcp:
    image: ghcr.io/alpacahq/alpaca-mcp-server:latest
    environment:
      - ALPACA_API_KEY=${ALPACA_API_KEY}
      - ALPACA_SECRET_KEY=${ALPACA_SECRET_KEY}
      - ALPACA_BASE_URL=${ALPACA_BASE_URL}
    ports:
      - "8000:8000"
    restart: unless-stopped

  scheduler:
    build: .
    volumes:
      - ./scripts:/app/scripts
      - ./logs:/app/logs
    env_file:
      - .env
    depends_on:
      - letta
      - alpaca-mcp
    restart: unless-stopped

volumes:
  letta-db:
```

- [ ] **Step 5: Create Dockerfile for scheduler service**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scheduler/ ./scheduler/
COPY scripts/ ./scripts/
CMD ["python", "-m", "scheduler.main"]
```

- [ ] **Step 6: Create package init files and conftest**

```bash
mkdir -p scheduler/tools tests/test_tools tests/test_indicators logs/sessions logs/errors scripts/indicators/trend scripts/indicators/momentum scripts/indicators/volatility scripts/indicators/volume scripts/indicators/composite scripts/analysis
touch scheduler/__init__.py scheduler/tools/__init__.py tests/__init__.py tests/test_tools/__init__.py tests/test_indicators/__init__.py
```

Create `tests/conftest.py`:

```python
# tests/conftest.py
import os
import pytest

@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Set dummy env vars so tools don't require real keys in unit tests."""
    monkeypatch.setenv("FMP_API_KEY", "test_fmp_key")
    monkeypatch.setenv("SERPER_API_KEY", "test_serper_key")
    monkeypatch.setenv("ALPACA_API_KEY", "test_alpaca_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_alpaca_secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_tg_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test_tg_chat")
    monkeypatch.setenv("LETTA_SERVER_URL", "http://localhost:8283")
    monkeypatch.setenv("SCRIPTS_DIR", "scripts")
```

- [ ] **Step 7: Verify Docker Compose config is valid**

```bash
docker compose config
```
Expected: printed merged config with no errors

- [ ] **Step 8: Commit**

```bash
git add requirements.txt .gitignore .env.example docker-compose.yml Dockerfile scheduler/__init__.py scheduler/tools/__init__.py tests/__init__.py tests/test_tools/__init__.py tests/test_indicators/__init__.py
git commit -m "chore: project scaffolding, Docker Compose, dependencies"
```

---

## Phase 2: Tools Layer

### Task 2: FMP tool

**Files:**
- Create: `scheduler/tools/fmp.py`
- Create: `tests/test_tools/test_fmp.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tools/test_fmp.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_tools/test_fmp.py -v
```
Expected: FAIL — `ImportError: cannot import name 'fmp_screener'`

- [ ] **Step 3: Implement fmp.py**

```python
# scheduler/tools/fmp.py
import os
import requests
from typing import Optional

FMP_BASE = "https://financialmodelingprep.com/api/v3"


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
    return _get("/stock-screener", {
        "marketCapMoreThan": market_cap_more_than,
        "volumeMoreThan": volume_more_than,
        "exchange": exchange,
        "limit": limit,
    }, api_key)


def fmp_ohlcv(ticker: str, limit: int = 90, api_key: Optional[str] = None) -> dict:
    """Get daily OHLCV data for a ticker. Returns dict with 'historical' list."""
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get(f"/historical-price-full/{ticker}", {"timeseries": limit}, api_key)


def fmp_news(tickers: list[str], limit: int = 10, api_key: Optional[str] = None) -> list:
    """Get recent news for a list of tickers."""
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get("/stock_news", {"tickers": ",".join(tickers), "limit": limit}, api_key)


def fmp_earnings_calendar(from_date: str, to_date: str, api_key: Optional[str] = None) -> list:
    """Get earnings announcements between two dates (YYYY-MM-DD format)."""
    api_key = api_key or os.environ["FMP_API_KEY"]
    return _get("/earning_calendar", {"from": from_date, "to": to_date}, api_key)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_tools/test_fmp.py -v
```
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/fmp.py tests/test_tools/test_fmp.py
git commit -m "feat: FMP tool — screener, OHLCV, news, earnings calendar"
```

---

### Task 3: Serper tool

**Files:**
- Create: `scheduler/tools/serper.py`
- Create: `tests/test_tools/test_serper.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tools/test_serper.py
import responses
import pytest
from scheduler.tools.serper import serper_search


@responses.activate
def test_serper_returns_results():
    responses.add(
        responses.POST,
        "https://google.serper.dev/search",
        json={"organic": [{"title": "Apple Q1 earnings beat", "link": "https://example.com", "snippet": "Apple reported..."}]},
        status=200,
    )
    result = serper_search(query="AAPL earnings 2026", api_key="test")
    assert "organic" in result
    assert result["organic"][0]["title"] == "Apple Q1 earnings beat"


@responses.activate
def test_serper_news_returns_results():
    responses.add(
        responses.POST,
        "https://google.serper.dev/news",
        json={"news": [{"title": "Fed raises rates", "link": "https://example.com"}]},
        status=200,
    )
    result = serper_search(query="Fed interest rates", search_type="news", api_key="test")
    assert "news" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_tools/test_serper.py -v
```
Expected: FAIL — `ImportError: cannot import name 'serper_search'`

- [ ] **Step 3: Implement serper.py**

```python
# scheduler/tools/serper.py
import os
import requests
from typing import Optional

SERPER_BASE = "https://google.serper.dev"


def serper_search(
    query: str,
    search_type: str = "search",
    num: int = 10,
    api_key: Optional[str] = None,
) -> dict:
    """
    Search the web via Serper (Google Search API).
    search_type: 'search' for general, 'news' for news results.
    Returns dict with 'organic' (search) or 'news' (news) list.
    """
    api_key = api_key or os.environ["SERPER_API_KEY"]
    response = requests.post(
        f"{SERPER_BASE}/{search_type}",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_tools/test_serper.py -v
```
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/serper.py tests/test_tools/test_serper.py
git commit -m "feat: Serper tool — web and news search"
```

---

### Task 4: PyExec tool

**Files:**
- Create: `scheduler/tools/pyexec.py`
- Create: `tests/test_tools/test_pyexec.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tools/test_pyexec.py
import pytest
from scheduler.tools.pyexec import run_script


def test_run_script_returns_stdout():
    result = run_script("print('hello')")
    assert result["stdout"] == "hello\n"
    assert result["returncode"] == 0
    assert result["error"] is None


def test_run_script_captures_json_output():
    script = "import json; print(json.dumps({'rsi': 65.4}))"
    result = run_script(script)
    assert '"rsi": 65.4' in result["stdout"]


def test_run_script_handles_syntax_error():
    result = run_script("def broken(:")
    assert result["returncode"] != 0
    assert result["error"] is not None


def test_run_script_enforces_timeout():
    result = run_script("import time; time.sleep(60)", timeout=1)
    assert result["returncode"] != 0
    assert "timeout" in result["error"].lower()


def test_run_script_blocks_network_import():
    result = run_script("import socket; s = socket.create_connection(('8.8.8.8', 80))", timeout=3)
    # Should either fail or be blocked — socket may be importable but connection refused in sandbox
    # We verify the script doesn't silently succeed and exfiltrate data
    assert result["returncode"] != 0 or "refused" in (result["error"] or "").lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_tools/test_pyexec.py -v
```
Expected: FAIL — `ImportError: cannot import name 'run_script'`

- [ ] **Step 3: Implement pyexec.py**

```python
# scheduler/tools/pyexec.py
import subprocess
import tempfile
import os
import sys
from typing import Optional


def run_script(
    code: str,
    timeout: int = 30,
    scripts_dir: Optional[str] = None,
) -> dict:
    """
    Execute a Python script in a sandboxed subprocess.
    Returns dict: {stdout, stderr, returncode, error}
    Constraints: timeout enforced, no network access (OS-level firewall on VPS),
    memory limited to 256MB via ulimit.
    """
    scripts_dir = scripts_dir or os.environ.get("SCRIPTS_DIR", "/app/scripts")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                "PYTHONPATH": scripts_dir,
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
            },
            preexec_fn=_set_resource_limits,
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "error": result.stderr if result.returncode != 0 else None,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": "Script execution timeout"}
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}
    finally:
        os.unlink(tmp_path)


def _set_resource_limits():
    """Called in subprocess before exec — limits memory to 256MB."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except Exception:
        pass  # Non-fatal: VPS firewall handles network isolation
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_tools/test_pyexec.py -v
```
Expected: 4-5 PASSED (network test may vary by OS; at minimum the first 4 must pass)

- [ ] **Step 5: Commit**

```bash
git add scheduler/tools/pyexec.py tests/test_tools/test_pyexec.py
git commit -m "feat: PyExec tool — sandboxed Python subprocess execution"
```

---

## Phase 3: Indicator Library

### Task 5: Core momentum indicators (RSI, MACD)

**Files:**
- Create: `scripts/indicators/momentum/rsi.py`
- Create: `scripts/indicators/momentum/macd.py`
- Create: `tests/test_indicators/test_rsi.py`
- Create: `tests/test_indicators/test_macd.py`

Each indicator script contract:
- **Input:** receives `data` (list of dicts with keys: `date`, `open`, `high`, `low`, `close`, `volume`) via `sys.stdin` as JSON, plus optional CLI args
- **Output:** prints JSON result to stdout

- [ ] **Step 1: Write failing tests**

```python
# tests/test_indicators/test_rsi.py
import subprocess, json, sys


SAMPLE_DATA = [
    {"date": f"2026-01-{i:02d}", "open": 100+i, "high": 102+i, "low": 99+i, "close": 101+i, "volume": 1000000}
    for i in range(1, 31)
]


def run_indicator(script_path: str, data: list, **kwargs) -> dict:
    args = [sys.executable, script_path]
    for k, v in kwargs.items():
        args += [f"--{k}", str(v)]
    result = subprocess.run(args, input=json.dumps(data), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    return json.loads(result.stdout)


def test_rsi_returns_value():
    result = run_indicator("scripts/indicators/momentum/rsi.py", SAMPLE_DATA, period=14)
    assert "rsi" in result
    assert 0 <= result["rsi"] <= 100


def test_rsi_overbought_signal():
    # Rising data should produce high RSI
    rising = [{"date": f"2026-01-{i:02d}", "open": 100+i*2, "high": 103+i*2, "low": 99+i*2, "close": 102+i*2, "volume": 1000000} for i in range(1, 31)]
    result = run_indicator("scripts/indicators/momentum/rsi.py", rising, period=14)
    assert result["rsi"] > 60
```

```python
# tests/test_indicators/test_macd.py
import subprocess, json, sys

SAMPLE_DATA = [
    {"date": f"2026-01-{i:02d}", "open": 100+i, "high": 102+i, "low": 99+i, "close": 101+i, "volume": 1000000}
    for i in range(1, 50)
]


def run_indicator(script_path: str, data: list, **kwargs) -> dict:
    args = [sys.executable, script_path]
    for k, v in kwargs.items():
        args += [f"--{k}", str(v)]
    result = subprocess.run(args, input=json.dumps(data), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, f"Script failed: {result.stderr}"
    return json.loads(result.stdout)


def test_macd_returns_components():
    result = run_indicator("scripts/indicators/momentum/macd.py", SAMPLE_DATA)
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_macd_histogram_is_difference():
    result = run_indicator("scripts/indicators/momentum/macd.py", SAMPLE_DATA)
    assert abs(result["histogram"] - (result["macd"] - result["signal"])) < 0.0001
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_indicators/test_rsi.py tests/test_indicators/test_macd.py -v
```
Expected: FAIL — scripts don't exist yet

- [ ] **Step 3: Implement rsi.py**

```python
#!/usr/bin/env python3
# scripts/indicators/momentum/rsi.py
# Input: JSON list of OHLCV dicts via stdin
# Args: --period (default 14)
# Output: {"rsi": float, "signal": "overbought"|"oversold"|"neutral"}

import sys, json, argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=14)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

delta = close.diff()
gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)
avg_gain = gain.ewm(com=args.period - 1, min_periods=args.period).mean()
avg_loss = loss.ewm(com=args.period - 1, min_periods=args.period).mean()
rs = avg_gain / avg_loss.replace(0, float("inf"))
rsi = float((100 - (100 / (1 + rs))).iloc[-1])

signal = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
print(json.dumps({"rsi": round(rsi, 2), "signal": signal, "period": args.period}))
```

- [ ] **Step 4: Implement macd.py**

```python
#!/usr/bin/env python3
# scripts/indicators/momentum/macd.py
# Input: JSON list of OHLCV dicts via stdin
# Args: --fast (12), --slow (26), --signal (9)
# Output: {"macd": float, "signal": float, "histogram": float, "crossover": "bullish"|"bearish"|"none"}

import sys, json, argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--fast", type=int, default=12)
parser.add_argument("--slow", type=int, default=26)
parser.add_argument("--signal", type=int, default=9)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

ema_fast = close.ewm(span=args.fast, adjust=False).mean()
ema_slow = close.ewm(span=args.slow, adjust=False).mean()
macd_line = ema_fast - ema_slow
signal_line = macd_line.ewm(span=args.signal, adjust=False).mean()
histogram = macd_line - signal_line

macd_val = float(macd_line.iloc[-1])
signal_val = float(signal_line.iloc[-1])
hist_val = float(histogram.iloc[-1])
prev_hist = float(histogram.iloc[-2]) if len(histogram) >= 2 else hist_val

crossover = "none"
if prev_hist < 0 and hist_val >= 0:
    crossover = "bullish"
elif prev_hist > 0 and hist_val <= 0:
    crossover = "bearish"

print(json.dumps({
    "macd": round(macd_val, 4),
    "signal": round(signal_val, 4),
    "histogram": round(hist_val, 4),
    "crossover": crossover,
}))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_indicators/test_rsi.py tests/test_indicators/test_macd.py -v
```
Expected: 4 PASSED

- [ ] **Step 6: Commit**

```bash
git add scripts/indicators/momentum/ tests/test_indicators/test_rsi.py tests/test_indicators/test_macd.py
git commit -m "feat: RSI and MACD momentum indicators"
```

---

### Task 6: Volatility and trend indicators (ATR, Bollinger Bands, EMA Crossover)

**Files:**
- Create: `scripts/indicators/volatility/atr.py`
- Create: `scripts/indicators/volatility/bollinger_bands.py`
- Create: `scripts/indicators/trend/ema_crossover.py`
- Create: `tests/test_indicators/test_atr.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_indicators/test_atr.py
import subprocess, json, sys

SAMPLE_DATA = [
    {"date": f"2026-01-{i:02d}", "open": 100+i, "high": 103+i, "low": 98+i, "close": 101+i, "volume": 1000000}
    for i in range(1, 31)
]


def run_indicator(script_path, data, **kwargs):
    args = [sys.executable, script_path]
    for k, v in kwargs.items():
        args += [f"--{k}", str(v)]
    result = subprocess.run(args, input=json.dumps(data), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_atr_returns_positive_value():
    result = run_indicator("scripts/indicators/volatility/atr.py", SAMPLE_DATA, period=14)
    assert "atr" in result
    assert result["atr"] > 0


def test_bollinger_bands_upper_above_lower():
    result = run_indicator("scripts/indicators/volatility/bollinger_bands.py", SAMPLE_DATA, period=20)
    assert result["upper"] > result["middle"] > result["lower"]


def test_ema_crossover_returns_signal():
    result = run_indicator("scripts/indicators/trend/ema_crossover.py", SAMPLE_DATA)
    assert result["signal"] in ("bullish", "bearish", "neutral")
    assert "fast_ema" in result and "slow_ema" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_indicators/test_atr.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement atr.py**

```python
#!/usr/bin/env python3
# scripts/indicators/volatility/atr.py
# Input: JSON OHLCV list via stdin | Args: --period (14)
# Output: {"atr": float, "atr_pct": float}

import sys, json, argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=14)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)

prev_close = close.shift(1)
tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
atr = float(tr.ewm(com=args.period - 1, min_periods=args.period).mean().iloc[-1])
atr_pct = round(atr / float(close.iloc[-1]) * 100, 2)

print(json.dumps({"atr": round(atr, 4), "atr_pct": atr_pct, "period": args.period}))
```

- [ ] **Step 4: Implement bollinger_bands.py**

```python
#!/usr/bin/env python3
# scripts/indicators/volatility/bollinger_bands.py
# Input: JSON OHLCV list via stdin | Args: --period (20), --std (2.0)
# Output: {"upper": float, "middle": float, "lower": float, "bandwidth": float, "position": "above_upper"|"below_lower"|"inside"}

import sys, json, argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=20)
parser.add_argument("--std", type=float, default=2.0)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

middle = close.rolling(args.period).mean()
std = close.rolling(args.period).std()
upper = middle + args.std * std
lower = middle - args.std * std

upper_val = round(float(upper.iloc[-1]), 4)
middle_val = round(float(middle.iloc[-1]), 4)
lower_val = round(float(lower.iloc[-1]), 4)
current = float(close.iloc[-1])
bandwidth = round((upper_val - lower_val) / middle_val * 100, 2)

position = "above_upper" if current > upper_val else "below_lower" if current < lower_val else "inside"
print(json.dumps({"upper": upper_val, "middle": middle_val, "lower": lower_val, "bandwidth": bandwidth, "position": position}))
```

- [ ] **Step 5: Implement ema_crossover.py**

```python
#!/usr/bin/env python3
# scripts/indicators/trend/ema_crossover.py
# Input: JSON OHLCV list via stdin | Args: --fast (9), --slow (21)
# Output: {"fast_ema": float, "slow_ema": float, "signal": "bullish"|"bearish"|"neutral", "crossover_today": bool}

import sys, json, argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--fast", type=int, default=9)
parser.add_argument("--slow", type=int, default=21)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

fast = close.ewm(span=args.fast, adjust=False).mean()
slow = close.ewm(span=args.slow, adjust=False).mean()

fast_now, slow_now = float(fast.iloc[-1]), float(slow.iloc[-1])
fast_prev, slow_prev = float(fast.iloc[-2]), float(slow.iloc[-2])

crossover_today = (fast_prev <= slow_prev and fast_now > slow_now) or (fast_prev >= slow_prev and fast_now < slow_now)
signal = "bullish" if fast_now > slow_now else "bearish" if fast_now < slow_now else "neutral"

print(json.dumps({"fast_ema": round(fast_now, 4), "slow_ema": round(slow_now, 4), "signal": signal, "crossover_today": crossover_today}))
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
pytest tests/test_indicators/test_atr.py -v
```
Expected: 3 PASSED

- [ ] **Step 7: Commit**

```bash
git add scripts/indicators/volatility/ scripts/indicators/trend/ema_crossover.py tests/test_indicators/test_atr.py
git commit -m "feat: ATR, Bollinger Bands, EMA Crossover indicators"
```

---

### Task 7: Composite indicators (Market Regime Detector, Relative Strength Scanner) and remaining indicators

**Files:**
- Create: `scripts/indicators/composite/market_regime_detector.py`
- Create: `scripts/indicators/composite/relative_strength_scanner.py`
- Create: `scripts/indicators/trend/adx_trend_strength.py`
- Create: `scripts/indicators/trend/supertrend.py`
- Create: `scripts/indicators/momentum/rate_of_change.py`
- Create: `scripts/indicators/volatility/vix_percentile.py`
- Create: `scripts/indicators/volume/vwap.py`
- Create: `scripts/indicators/volume/obv.py`
- Create: `scripts/indicators/volume/volume_profile.py`
- Create: `scripts/indicators/index.json`
- Create: `tests/test_indicators/test_market_regime_detector.py`

- [ ] **Step 1: Write failing tests for market regime detector**

```python
# tests/test_indicators/test_market_regime_detector.py
import subprocess, json, sys


def run_indicator(script_path, data, **kwargs):
    args = [sys.executable, script_path]
    for k, v in kwargs.items():
        args += [f"--{k}", str(v)]
    result = subprocess.run(args, input=json.dumps(data), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_market_regime_detector_returns_regime():
    # Simulate SPY and VIX data
    spy_data = [{"date": f"2026-01-{i:02d}", "open": 490+i, "high": 495+i, "low": 488+i, "close": 492+i, "volume": 80000000} for i in range(1, 60)]
    vix_data = [{"date": f"2026-01-{i:02d}", "open": 15.0, "high": 16.0, "low": 14.0, "close": 15.5, "volume": 0} for i in range(1, 60)]
    payload = {"spy": spy_data, "vix": vix_data}
    result = run_indicator("scripts/indicators/composite/market_regime_detector.py", payload)
    assert result["regime"] in ("bull_low_vol", "bull_high_vol", "bear_low_vol", "bear_high_vol", "range_low_vol", "range_high_vol")
    assert "vix_current" in result
    assert "spy_trend" in result


def test_relative_strength_scanner_returns_ranked_list():
    tickers_data = {
        "AAPL": [{"date": f"2026-01-{i:02d}", "close": 180+i*1.5} for i in range(1, 30)],
        "MSFT": [{"date": f"2026-01-{i:02d}", "close": 400+i*0.5} for i in range(1, 30)],
        "GOOGL": [{"date": f"2026-01-{i:02d}", "close": 170+i*2.0} for i in range(1, 30)],
    }
    result = subprocess.run(
        [sys.executable, "scripts/indicators/composite/relative_strength_scanner.py"],
        input=json.dumps(tickers_data),
        capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "ranked" in output
    assert len(output["ranked"]) == 3
    assert output["ranked"][0]["ticker"] in ("AAPL", "MSFT", "GOOGL")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_indicators/test_market_regime_detector.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement market_regime_detector.py**

```python
#!/usr/bin/env python3
# scripts/indicators/composite/market_regime_detector.py
# Input: JSON {"spy": [ohlcv...], "vix": [ohlcv...]} via stdin
# Output: {"regime": str, "spy_trend": "up"|"down"|"sideways", "vix_current": float, "vix_percentile_52w": float, "description": str}

import sys, json
import pandas as pd

data = json.load(sys.stdin)

spy_df = pd.DataFrame(data["spy"]).sort_values("date")
vix_df = pd.DataFrame(data["vix"]).sort_values("date")

spy_close = spy_df["close"].astype(float)
spy_ma50 = spy_close.rolling(50).mean().iloc[-1] if len(spy_close) >= 50 else spy_close.mean()
spy_ma20 = spy_close.rolling(20).mean().iloc[-1] if len(spy_close) >= 20 else spy_close.mean()
spy_current = float(spy_close.iloc[-1])

if spy_current > spy_ma50 and spy_ma20 > spy_ma50:
    spy_trend = "up"
elif spy_current < spy_ma50 and spy_ma20 < spy_ma50:
    spy_trend = "down"
else:
    spy_trend = "sideways"

vix_close = vix_df["close"].astype(float)
vix_current = float(vix_close.iloc[-1])
vix_52w = vix_close.tail(252) if len(vix_close) >= 252 else vix_close
vix_pct = float((vix_52w <= vix_current).mean() * 100)
high_vol = vix_current > 20

regime_map = {
    ("up", False): "bull_low_vol",
    ("up", True): "bull_high_vol",
    ("down", False): "bear_low_vol",
    ("down", True): "bear_high_vol",
    ("sideways", False): "range_low_vol",
    ("sideways", True): "range_high_vol",
}
regime = regime_map[(spy_trend, high_vol)]

descriptions = {
    "bull_low_vol": "Trending bull market with low volatility — momentum strategies favored",
    "bull_high_vol": "Bull market but elevated volatility — tighter stops, smaller size",
    "bear_low_vol": "Downtrend with low volatility — mean reversion or cash",
    "bear_high_vol": "Bear market with high volatility — defensive, minimal exposure",
    "range_low_vol": "Sideways market, low volatility — range-bound strategies",
    "range_high_vol": "Choppy market — high caution, reduce position frequency",
}

print(json.dumps({
    "regime": regime,
    "spy_trend": spy_trend,
    "spy_current": round(spy_current, 2),
    "spy_ma50": round(float(spy_ma50), 2),
    "vix_current": round(vix_current, 2),
    "vix_percentile_52w": round(vix_pct, 1),
    "high_volatility": high_vol,
    "description": descriptions[regime],
}))
```

- [ ] **Step 4: Implement relative_strength_scanner.py**

```python
#!/usr/bin/env python3
# scripts/indicators/composite/relative_strength_scanner.py
# Input: JSON {"TICKER": [{"date": ..., "close": ...}, ...], ...} via stdin
# Output: {"ranked": [{"ticker": str, "rs_score": float, "return_pct": float}, ...], "top_picks": [str]}

import sys, json
import pandas as pd

data = json.load(sys.stdin)
scores = []

for ticker, ohlcv in data.items():
    df = pd.DataFrame(ohlcv).sort_values("date")
    close = df["close"].astype(float)
    if len(close) < 2:
        continue
    ret_pct = (close.iloc[-1] - close.iloc[0]) / close.iloc[0] * 100
    # Weight recent performance more heavily (last 25% of period)
    split = max(1, len(close) * 3 // 4)
    recent_ret = (close.iloc[-1] - close.iloc[split]) / close.iloc[split] * 100
    rs_score = round(ret_pct * 0.4 + recent_ret * 0.6, 2)
    scores.append({"ticker": ticker, "rs_score": rs_score, "return_pct": round(ret_pct, 2)})

ranked = sorted(scores, key=lambda x: x["rs_score"], reverse=True)
top_picks = [r["ticker"] for r in ranked[:5]]

print(json.dumps({"ranked": ranked, "top_picks": top_picks}))
```

- [ ] **Step 5: Implement remaining indicator stubs**

```python
# scripts/indicators/trend/adx_trend_strength.py
#!/usr/bin/env python3
# Input: JSON OHLCV list via stdin | Args: --period (14)
# Output: {"adx": float, "trend_strength": "strong"|"moderate"|"weak", "di_plus": float, "di_minus": float}
import sys, json, argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=14)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)

tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
dm_plus = (high.diff()).clip(lower=0).where(high.diff() > low.diff().abs(), 0)
dm_minus = (-low.diff()).clip(lower=0).where(low.diff().abs() > high.diff(), 0)

atr = tr.ewm(com=args.period - 1, min_periods=args.period).mean()
di_plus = 100 * dm_plus.ewm(com=args.period - 1, min_periods=args.period).mean() / atr
di_minus = 100 * dm_minus.ewm(com=args.period - 1, min_periods=args.period).mean() / atr
dx = (100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, 1))
adx = float(dx.ewm(com=args.period - 1, min_periods=args.period).mean().iloc[-1])

strength = "strong" if adx > 25 else "moderate" if adx > 20 else "weak"
print(json.dumps({"adx": round(adx, 2), "trend_strength": strength, "di_plus": round(float(di_plus.iloc[-1]), 2), "di_minus": round(float(di_minus.iloc[-1]), 2)}))
```

```python
# scripts/indicators/momentum/rate_of_change.py
#!/usr/bin/env python3
# Input: JSON OHLCV via stdin | Args: --period (10)
# Output: {"roc": float, "signal": "accelerating"|"decelerating"|"flat"}
import sys, json, argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=10)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

roc = float(((close.iloc[-1] - close.iloc[-args.period]) / close.iloc[-args.period]) * 100)
prev_roc = float(((close.iloc[-2] - close.iloc[-args.period - 1]) / close.iloc[-args.period - 1]) * 100) if len(close) > args.period + 1 else roc
signal = "accelerating" if roc > prev_roc else "decelerating" if roc < prev_roc else "flat"
print(json.dumps({"roc": round(roc, 2), "prev_roc": round(prev_roc, 2), "signal": signal}))
```

```python
# scripts/indicators/volatility/vix_percentile.py
#!/usr/bin/env python3
# Input: JSON list of VIX close values {"date": ..., "close": ...} via stdin
# Output: {"vix_current": float, "percentile_52w": float, "regime": "low"|"normal"|"elevated"|"extreme"}
import sys, json
import pandas as pd

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)
vix = float(close.iloc[-1])
window = close.tail(252) if len(close) >= 252 else close
pct = round(float((window <= vix).mean() * 100), 1)
regime = "extreme" if vix > 40 else "elevated" if vix > 25 else "normal" if vix > 15 else "low"
print(json.dumps({"vix_current": round(vix, 2), "percentile_52w": pct, "regime": regime}))
```

```python
# scripts/indicators/volume/vwap.py
#!/usr/bin/env python3
# Input: JSON OHLCV list via stdin (uses today's bars if intraday, or last N days)
# Output: {"vwap": float, "position": "above"|"below", "deviation_pct": float}
import sys, json
import pandas as pd

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
typical_price = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3
volume = df["volume"].astype(float)
vwap = float((typical_price * volume).sum() / volume.sum())
current = float(df["close"].astype(float).iloc[-1])
deviation_pct = round((current - vwap) / vwap * 100, 2)
position = "above" if current > vwap else "below"
print(json.dumps({"vwap": round(vwap, 4), "position": position, "deviation_pct": deviation_pct}))
```

```python
# scripts/indicators/volume/obv.py
#!/usr/bin/env python3
# Input: JSON OHLCV via stdin
# Output: {"obv": float, "obv_trend": "rising"|"falling"|"flat", "obv_divergence": bool}
import sys, json
import pandas as pd

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)
volume = df["volume"].astype(float)
direction = close.diff().apply(lambda x: 1 if x > 0 else -1 if x < 0 else 0)
obv = (direction * volume).cumsum()
obv_now = float(obv.iloc[-1])
obv_5d_ago = float(obv.iloc[-6]) if len(obv) > 5 else float(obv.iloc[0])
obv_trend = "rising" if obv_now > obv_5d_ago else "falling" if obv_now < obv_5d_ago else "flat"
price_up = close.iloc[-1] > close.iloc[-6] if len(close) > 5 else True
obv_divergence = (price_up and obv_trend == "falling") or (not price_up and obv_trend == "rising")
print(json.dumps({"obv": round(obv_now, 0), "obv_trend": obv_trend, "obv_divergence": obv_divergence}))
```

```python
# scripts/indicators/volume/volume_profile.py
#!/usr/bin/env python3
# Input: JSON OHLCV via stdin | Args: --bins (10)
# Output: {"poc": float, "value_area_high": float, "value_area_low": float, "high_volume_nodes": [float]}
import sys, json, argparse
import pandas as pd
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--bins", type=int, default=10)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
prices = df["close"].astype(float)
volumes = df["volume"].astype(float)

price_min, price_max = prices.min(), prices.max()
bin_edges = np.linspace(price_min, price_max, args.bins + 1)
bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

vol_profile = np.zeros(args.bins)
for price, vol in zip(prices, volumes):
    idx = min(int((price - price_min) / (price_max - price_min + 1e-9) * args.bins), args.bins - 1)
    vol_profile[idx] += vol

poc_idx = int(np.argmax(vol_profile))
poc = round(float(bin_centers[poc_idx]), 2)
total_vol = vol_profile.sum()
va_vol = total_vol * 0.7
sorted_idx = np.argsort(vol_profile)[::-1]
va_indices, acc = [], 0
for i in sorted_idx:
    if acc >= va_vol:
        break
    va_indices.append(i)
    acc += vol_profile[i]

va_prices = [bin_centers[i] for i in va_indices]
hvn = [round(float(bin_centers[i]), 2) for i in sorted_idx[:3]]
print(json.dumps({"poc": poc, "value_area_high": round(float(max(va_prices)), 2), "value_area_low": round(float(min(va_prices)), 2), "high_volume_nodes": hvn}))
```

```python
# scripts/indicators/trend/supertrend.py
#!/usr/bin/env python3
# Input: JSON OHLCV via stdin | Args: --period (10), --multiplier (3.0)
# Output: {"supertrend": float, "signal": "buy"|"sell", "trend": "up"|"down"}
import sys, json, argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=10)
parser.add_argument("--multiplier", type=float, default=3.0)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date").reset_index(drop=True)
high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)

prev_close = close.shift(1)
tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
atr = tr.ewm(com=args.period - 1, min_periods=args.period).mean()
hl2 = (high + low) / 2
upper_band = hl2 + args.multiplier * atr
lower_band = hl2 - args.multiplier * atr

supertrend = pd.Series(index=df.index, dtype=float)
direction = pd.Series(index=df.index, dtype=int)
for i in range(1, len(df)):
    if close.iloc[i] > upper_band.iloc[i]:
        direction.iloc[i] = 1
    elif close.iloc[i] < lower_band.iloc[i]:
        direction.iloc[i] = -1
    else:
        direction.iloc[i] = direction.iloc[i - 1] if i > 0 else 1
    supertrend.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == 1 else upper_band.iloc[i]

st_val = round(float(supertrend.iloc[-1]), 4)
trend = "up" if direction.iloc[-1] == 1 else "down"
signal = "buy" if trend == "up" else "sell"
print(json.dumps({"supertrend": st_val, "signal": signal, "trend": trend}))
```

- [ ] **Step 6: Create scripts/indicators/index.json**

```json
{
  "indicators": {
    "rsi": {"path": "indicators/momentum/rsi.py", "inputs": "ohlcv_list", "args": "--period INT(14)", "created": "2026-04-10", "notes": "Reliable in ranging markets. >70 overbought, <30 oversold."},
    "macd": {"path": "indicators/momentum/macd.py", "inputs": "ohlcv_list", "args": "--fast INT(12) --slow INT(26) --signal INT(9)", "created": "2026-04-10", "notes": "Trend and momentum. Watch crossover field."},
    "rate_of_change": {"path": "indicators/momentum/rate_of_change.py", "inputs": "ohlcv_list", "args": "--period INT(10)", "created": "2026-04-10", "notes": "Price momentum over N periods."},
    "atr": {"path": "indicators/volatility/atr.py", "inputs": "ohlcv_list", "args": "--period INT(14)", "created": "2026-04-10", "notes": "Use atr_pct for stop-loss sizing (e.g., 1.5x ATR%)."},
    "bollinger_bands": {"path": "indicators/volatility/bollinger_bands.py", "inputs": "ohlcv_list", "args": "--period INT(20) --std FLOAT(2.0)", "created": "2026-04-10", "notes": "Squeeze (low bandwidth) often precedes breakout."},
    "vix_percentile": {"path": "indicators/volatility/vix_percentile.py", "inputs": "vix_close_list", "args": "none", "created": "2026-04-10", "notes": "Run with VIX OHLCV data. Regime: low<15, normal 15-25, elevated 25-40, extreme>40."},
    "ema_crossover": {"path": "indicators/trend/ema_crossover.py", "inputs": "ohlcv_list", "args": "--fast INT(9) --slow INT(21)", "created": "2026-04-10", "notes": "Primary trend filter. crossover_today=true is the signal."},
    "adx_trend_strength": {"path": "indicators/trend/adx_trend_strength.py", "inputs": "ohlcv_list", "args": "--period INT(14)", "created": "2026-04-10", "notes": "ADX>25 = strong trend. Use to filter momentum trades."},
    "supertrend": {"path": "indicators/trend/supertrend.py", "inputs": "ohlcv_list", "args": "--period INT(10) --multiplier FLOAT(3.0)", "created": "2026-04-10", "notes": "Dynamic support/resistance and trend signal."},
    "vwap": {"path": "indicators/volume/vwap.py", "inputs": "ohlcv_list", "args": "none", "created": "2026-04-10", "notes": "Use as dynamic S/R level and entry filter."},
    "obv": {"path": "indicators/volume/obv.py", "inputs": "ohlcv_list", "args": "none", "created": "2026-04-10", "notes": "obv_divergence=true is a reversal warning."},
    "volume_profile": {"path": "indicators/volume/volume_profile.py", "inputs": "ohlcv_list", "args": "--bins INT(10)", "created": "2026-04-10", "notes": "POC = Point of Control (highest volume price). Use for S/R."},
    "market_regime_detector": {"path": "indicators/composite/market_regime_detector.py", "inputs": "{spy: ohlcv_list, vix: ohlcv_list}", "args": "none", "created": "2026-04-10", "notes": "Run pre-market daily. Regime drives strategy adaptation."},
    "relative_strength_scanner": {"path": "indicators/composite/relative_strength_scanner.py", "inputs": "{TICKER: [{date, close}], ...}", "args": "none", "created": "2026-04-10", "notes": "Pass candidate tickers, returns ranked list by RS score."}
  },
  "analysis": {}
}
```

- [ ] **Step 7: Run all indicator tests**

```bash
pytest tests/test_indicators/ -v
```
Expected: all PASSED

- [ ] **Step 8: Commit**

```bash
git add scripts/indicators/ tests/test_indicators/test_market_regime_detector.py
git commit -m "feat: complete indicator library — composite, volume, trend, remaining momentum"
```

---

## Phase 4: Letta Agent Setup

### Task 8: Letta client wrapper

**Files:**
- Create: `scheduler/agent.py`
- Create: `tests/test_agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agent.py
from unittest.mock import MagicMock, patch
import pytest
from scheduler.agent import LettaTraderAgent


def test_agent_sends_message_and_returns_response():
    with patch("scheduler.agent.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client
        mock_response = MagicMock()
        mock_response.messages = [MagicMock(text='{"status": "ok", "trades": []}')]
        mock_client.send_message.return_value = mock_response

        agent = LettaTraderAgent(agent_id="test-agent-id")
        result = agent.send_session("SESSION: market_open | DATE: 2026-04-10")

        mock_client.send_message.assert_called_once()
        assert result is not None


def test_agent_get_core_memory_block():
    with patch("scheduler.agent.create_client") as mock_create:
        mock_client = MagicMock()
        mock_create.return_value = mock_client
        mock_block = MagicMock()
        mock_block.value = '{"version": "v1"}'
        mock_client.get_in_context_memory.return_value = MagicMock(blocks=[mock_block])

        agent = LettaTraderAgent(agent_id="test-agent-id")
        # Should not raise
        block = agent.get_memory_block("strategy_doc")
        assert block is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_agent.py -v
```
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement agent.py**

```python
# scheduler/agent.py
import os
from typing import Optional
from letta import create_client
from letta.schemas.memory import BasicBlockMemory
from letta.schemas.block import Block
from letta.schemas.llm_config import LLMConfig
from letta.schemas.embedding_config import EmbeddingConfig


INITIAL_STRATEGY_DOC = """# Strategy Document v1
## Philosophy
Trade US equities on the 1D timeframe. All values below are starting defaults — override with reasoning.

## Approach
Momentum-first. Look for stocks with strong relative strength, clear trend, and volume confirmation.

## Entry Criteria (defaults)
- Price above 50-day EMA
- RSI between 40-70 (not overbought at entry)
- Volume above 20-day average
- Market regime: bull or range (not bear_high_vol)

## Exit Criteria (defaults)
- Stop loss: 1.5x ATR below entry
- Take profit: 3x ATR above entry (minimum 2:1 R:R)
- Trail stop after 1.5R profit

## Position Sizing (defaults)
- Risk per trade: 1% of account
- Max open positions: 5
- Max position size: 15% of account
- Max daily loss: 3% of account

## Session Responsibilities
- pre_market: screen stocks, assess regime, build today's watchlist and thesis
- market_open: execute planned trades, set stops and targets
- health_check: monitor open positions, check news, close if thesis invalidated
- eod_reflection: review trades, update hypotheses, evolve strategy if needed
- weekly_review: deep pattern mining, prune watchlist, compress memory

## Market Regime
unknown — assess on first pre_market session
"""

INITIAL_PERFORMANCE_SNAPSHOT = """{
  "trades_total": 0,
  "win_rate_10": null,
  "win_rate_20": null,
  "avg_rr": null,
  "current_drawdown_pct": 0.0,
  "peak_equity": 50000.0,
  "current_equity": 50000.0,
  "pivot_alerts": []
}"""

INITIAL_WATCHLIST = """# Watchlist
Empty on bootstrap — populated during pre_market sessions.
Format per entry: TICKER | thesis | date_added | confidence (1-10)
"""

INITIAL_TODAY_CONTEXT = """# Today's Context
Not yet populated — will be written during pre_market session.
"""


class LettaTraderAgent:
    def __init__(self, agent_id: str, server_url: Optional[str] = None):
        self.agent_id = agent_id
        self.client = create_client(base_url=server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283"))

    def send_session(self, prompt: str) -> str:
        """Send a session prompt to the agent. Returns the agent's last message text."""
        response = self.client.send_message(
            agent_id=self.agent_id,
            message=prompt,
            role="user",
        )
        texts = [m.text for m in response.messages if hasattr(m, "text") and m.text]
        return texts[-1] if texts else ""

    def get_memory_block(self, block_name: str) -> Optional[str]:
        """Read a named core memory block."""
        memory = self.client.get_in_context_memory(agent_id=self.agent_id)
        for block in memory.blocks:
            if block.label == block_name:
                return block.value
        return None

    @classmethod
    def create_new(cls, agent_name: str, server_url: Optional[str] = None) -> "LettaTraderAgent":
        """Create a brand-new Letta agent with initialized memory. Used by bootstrap only."""
        server_url = server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
        client = create_client(base_url=server_url)

        memory = BasicBlockMemory(blocks=[
            Block(label="strategy_doc", value=INITIAL_STRATEGY_DOC, limit=4000),
            Block(label="watchlist", value=INITIAL_WATCHLIST, limit=2000),
            Block(label="performance_snapshot", value=INITIAL_PERFORMANCE_SNAPSHOT, limit=1000),
            Block(label="today_context", value=INITIAL_TODAY_CONTEXT, limit=2000),
        ])

        agent = client.create_agent(
            name=agent_name,
            llm_config=LLMConfig(
                model="claude-sonnet-4-6",
                model_endpoint_type="anthropic",
                model_endpoint="https://api.anthropic.com/v1",
                context_window=200000,
            ),
            # embedding_config omitted — uses Letta server defaults (no OpenAI key needed)
            memory=memory,
        )
        return cls(agent_id=agent.id, server_url=server_url)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_agent.py -v
```
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add scheduler/agent.py tests/test_agent.py
git commit -m "feat: Letta agent wrapper — create, send session, read memory"
```

---

### Task 9: Tool registry and bootstrap

**Files:**
- Create: `scheduler/tools/registry.py`
- Create: `scheduler/bootstrap.py`

- [ ] **Step 1: Implement registry.py**

```python
# scheduler/tools/registry.py
"""
Registers FMP, Serper, and PyExec as Letta agent tools.
Alpaca is accessed via the Alpaca MCP server — Letta connects to it as an MCP source,
not a registered Python tool.
"""
import os
import json
from letta import create_client
from scheduler.tools.fmp import fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar
from scheduler.tools.serper import serper_search
from scheduler.tools.pyexec import run_script


def register_all_tools(agent_id: str, server_url: str | None = None) -> list[str]:
    """
    Register FMP, Serper, and PyExec tools with the Letta agent.
    Returns list of registered tool names.
    """
    client = create_client(base_url=server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283"))
    registered = []

    for fn in [fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar, serper_search, run_script]:
        try:
            tool = client.create_tool(fn)
            client.add_tool_to_agent(agent_id=agent_id, tool_id=tool.id)
            registered.append(tool.name)
        except Exception as e:
            print(f"Warning: could not register tool {fn.__name__}: {e}")

    return registered


def attach_alpaca_mcp(agent_id: str, server_url: str | None = None) -> bool:
    """
    Attach the Alpaca MCP server as a tool source for the agent.
    The Alpaca MCP server runs at ALPACA_MCP_URL (default: http://alpaca-mcp:8000/sse).
    """
    client = create_client(base_url=server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283"))
    alpaca_mcp_url = os.environ.get("ALPACA_MCP_URL", "http://alpaca-mcp:8000/sse")
    try:
        client.add_tool_source(
            agent_id=agent_id,
            source_type="mcp",
            source_url=alpaca_mcp_url,
        )
        return True
    except Exception as e:
        print(f"Warning: could not attach Alpaca MCP: {e}")
        return False
```

- [ ] **Step 2: Implement bootstrap.py**

```python
# scheduler/bootstrap.py
"""
One-time bootstrap. Creates the Letta agent, registers tools, and seeds memory.
Run manually: python -m scheduler.bootstrap
Never run again after first successful execution.
"""
import os
import json
from pathlib import Path
from scheduler.agent import LettaTraderAgent
from scheduler.tools.registry import register_all_tools, attach_alpaca_mcp

AGENT_ID_FILE = Path("/app/.agent_id")


def bootstrap():
    if AGENT_ID_FILE.exists():
        print("Bootstrap already completed. Agent ID:", AGENT_ID_FILE.read_text().strip())
        return

    agent_name = os.environ.get("LETTA_AGENT_NAME", "claude_trader")
    print(f"Creating Letta agent '{agent_name}'...")
    agent = LettaTraderAgent.create_new(agent_name)
    print(f"Agent created: {agent.agent_id}")

    print("Registering tools...")
    tools = register_all_tools(agent.agent_id)
    print(f"Registered: {tools}")

    print("Attaching Alpaca MCP server...")
    ok = attach_alpaca_mcp(agent.agent_id)
    print(f"Alpaca MCP attached: {ok}")

    # Load script library index into agent memory
    index_path = Path("/app/scripts/indicators/index.json")
    if index_path.exists():
        index_content = index_path.read_text()
        response = agent.send_session(
            f"BOOTSTRAP: Load this indicator library index into your memory for future reference.\n\n{index_content}"
        )
        print("Indicator library loaded.")

    # Save agent ID for scheduler use
    AGENT_ID_FILE.write_text(agent.agent_id)
    print(f"Bootstrap complete. Agent ID saved to {AGENT_ID_FILE}")


if __name__ == "__main__":
    bootstrap()
```

- [ ] **Step 3: Commit**

```bash
git add scheduler/tools/registry.py scheduler/bootstrap.py
git commit -m "feat: tool registry and bootstrap session"
```

---

## Phase 5: Scheduler and Sessions

### Task 10: Session prompt builders

**Files:**
- Create: `scheduler/sessions.py`
- Create: `tests/test_sessions.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sessions.py
from unittest.mock import patch, MagicMock
from scheduler.sessions import (
    build_pre_market_prompt,
    build_market_open_prompt,
    build_health_check_prompt,
    build_eod_reflection_prompt,
    build_weekly_review_prompt,
)


def test_pre_market_prompt_contains_session_type():
    prompt = build_pre_market_prompt(date="2026-04-10", market_opens_in="3h30m")
    assert "SESSION: pre_market" in prompt
    assert "2026-04-10" in prompt


def test_health_check_prompt_injects_positions():
    positions = [{"symbol": "NVDA", "qty": 10, "current_price": 900.0}]
    prompt = build_health_check_prompt(date="2026-04-10", positions=positions)
    assert "SESSION: health_check" in prompt
    assert "NVDA" in prompt


def test_eod_prompt_injects_trades():
    trades = [{"symbol": "NVDA", "side": "buy", "qty": 10, "filled_avg_price": 891.0, "status": "filled"}]
    prompt = build_eod_reflection_prompt(date="2026-04-10", trades_today=trades)
    assert "SESSION: eod_reflection" in prompt
    assert "NVDA" in prompt


def test_market_open_prompt_contains_time():
    prompt = build_market_open_prompt(date="2026-04-10", time_et="09:30")
    assert "SESSION: market_open" in prompt
    assert "09:30" in prompt


def test_weekly_review_prompt_contains_week():
    prompt = build_weekly_review_prompt(date="2026-04-13", week_number=16)
    assert "SESSION: weekly_review" in prompt
    assert "16" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_sessions.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement sessions.py**

```python
# scheduler/sessions.py
import json
from typing import Optional


def build_pre_market_prompt(date: str, market_opens_in: str) -> str:
    return f"SESSION: pre_market | DATE: {date} | MARKET_OPENS_IN: {market_opens_in}"


def build_market_open_prompt(date: str, time_et: str) -> str:
    return f"SESSION: market_open | DATE: {date} | TIME: {time_et} ET"


def build_health_check_prompt(date: str, positions: list) -> str:
    positions_json = json.dumps(positions)
    return f"SESSION: health_check | DATE: {date} | TIME: 13:00 ET | POSITIONS: {positions_json}"


def build_eod_reflection_prompt(date: str, trades_today: list) -> str:
    trades_json = json.dumps(trades_today)
    return f"SESSION: eod_reflection | DATE: {date} | TIME: 15:45 ET | TRADES_TODAY: {trades_json}"


def build_weekly_review_prompt(date: str, week_number: int) -> str:
    return f"SESSION: weekly_review | DATE: {date} | WEEK: {week_number}"
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_sessions.py -v
```
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add scheduler/sessions.py tests/test_sessions.py
git commit -m "feat: session prompt builders with dynamic injection"
```

---

### Task 11: Telegram notifier

**Files:**
- Create: `scheduler/notifier.py`
- Create: `tests/test_notifier.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_notifier.py
from unittest.mock import patch, MagicMock
import pytest
from scheduler.notifier import (
    parse_session_output,
    format_trade_notification,
    format_eod_summary,
    format_error_notification,
    send_telegram,
)


def test_parse_session_output_extracts_json():
    raw = 'Some reasoning text...\n{"status": "ok", "trades": [{"symbol": "NVDA", "side": "buy", "qty": 10}]}'
    result = parse_session_output(raw)
    assert result["status"] == "ok"
    assert result["trades"][0]["symbol"] == "NVDA"


def test_parse_session_output_handles_no_json():
    result = parse_session_output("No JSON here, just text")
    assert result == {}


def test_format_trade_notification():
    trade = {"symbol": "NVDA", "side": "buy", "qty": 47, "filled_avg_price": 891.20, "stop": 872.0, "target": 945.0, "risk_pct": 1.8}
    msg = format_trade_notification(trade)
    assert "NVDA" in msg
    assert "891.20" in msg
    assert "872.0" in msg


def test_format_eod_summary():
    summary = {"date": "2026-04-10", "trades": 3, "pnl": 1240.0, "win_rate_10": 60.0, "avg_rr": 1.8, "strategy_version": "v4", "strategy_changed": False, "lesson": "Momentum works when volume confirms."}
    msg = format_eod_summary(summary)
    assert "Apr 10" in msg or "2026-04-10" in msg
    assert "1,240" in msg or "1240" in msg
    assert "60" in msg


def test_format_error_notification():
    msg = format_error_notification(session="eod_reflection", error="Letta timeout")
    assert "eod_reflection" in msg
    assert "Letta timeout" in msg


@patch("scheduler.notifier.requests.post")
def test_send_telegram_calls_api(mock_post):
    mock_post.return_value = MagicMock(status_code=200)
    send_telegram("test message", bot_token="token123", chat_id="456")
    mock_post.assert_called_once()
    call_kwargs = mock_post.call_args
    assert "token123" in str(call_kwargs)
    assert "test message" in str(call_kwargs)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_notifier.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement notifier.py**

```python
# scheduler/notifier.py
import os
import re
import json
import requests
from typing import Optional


def parse_session_output(raw: str) -> dict:
    """
    Extract the last JSON object from Claude's session output.
    Claude emits a structured JSON block at the end of each session.
    """
    matches = re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL)
    for match in reversed(matches):
        try:
            return json.loads(match)
        except json.JSONDecodeError:
            continue
    return {}


def format_trade_notification(trade: dict) -> str:
    side = trade.get("side", "").upper()
    symbol = trade.get("symbol", "?")
    qty = trade.get("qty", 0)
    price = trade.get("filled_avg_price", 0)
    stop = trade.get("stop", "?")
    target = trade.get("target", "?")
    risk = trade.get("risk_pct", "?")
    return (
        f"📈 TRADE EXECUTED\n"
        f"{symbol} | {side} | {qty} shares @ ${price:.2f}\n"
        f"Stop: ${stop} | Target: ${target}\n"
        f"Risk: {risk}%"
    )


def format_eod_summary(summary: dict) -> str:
    date = summary.get("date", "?")
    trades = summary.get("trades", 0)
    pnl = summary.get("pnl", 0)
    win_rate = summary.get("win_rate_10", "?")
    avg_rr = summary.get("avg_rr", "?")
    version = summary.get("strategy_version", "?")
    changed = " (UPDATED)" if summary.get("strategy_changed") else " (unchanged)"
    lesson = summary.get("lesson", "")
    pnl_str = f"+${pnl:,.0f}" if pnl >= 0 else f"-${abs(pnl):,.0f}"
    return (
        f"📊 EOD SUMMARY — {date}\n"
        f"Trades: {trades} | P&L: {pnl_str}\n"
        f"Win rate (10): {win_rate}% | Avg R:R: {avg_rr}\n"
        f"Strategy: {version}{changed}\n"
        f"Lesson: {lesson}"
    )


def format_strategy_update(update: dict) -> str:
    version = update.get("new_version", "?")
    trigger = update.get("trigger", "?")
    change = update.get("change", "?")
    note = update.get("diagnostic_note", "")
    return (
        f"🔄 STRATEGY UPDATED → {version}\n"
        f"Trigger: {trigger}\n"
        f"Change: {change}\n"
        f"{note}"
    )


def format_error_notification(session: str, error: str) -> str:
    return (
        f"❌ SESSION ERROR\n"
        f"{session} failed: {error}\n"
        f"Action: retrying in 60s"
    )


def format_alert(message: str) -> str:
    return f"⚠️ ALERT\n{message}"


def send_telegram(
    message: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """Send a Telegram message. Returns True on success."""
    bot_token = bot_token or os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = chat_id or os.environ["TELEGRAM_CHAT_ID"]
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_notifier.py -v
```
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add scheduler/notifier.py tests/test_notifier.py
git commit -m "feat: Telegram notifier — parse Claude output, format messages, send"
```

---

### Task 12: APScheduler main entrypoint

**Files:**
- Create: `scheduler/main.py`

- [ ] **Step 1: Implement main.py**

```python
# scheduler/main.py
"""
Main scheduler entrypoint.
Runs 5 recurring triggers + handles session dispatch, error recovery, and notifications.
"""
import os
import json
import time
import logging
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from scheduler.agent import LettaTraderAgent
from scheduler.sessions import (
    build_pre_market_prompt,
    build_market_open_prompt,
    build_health_check_prompt,
    build_eod_reflection_prompt,
    build_weekly_review_prompt,
)
from scheduler.notifier import (
    parse_session_output,
    format_trade_notification,
    format_eod_summary,
    format_error_notification,
    format_alert,
    send_telegram,
)

ET = ZoneInfo("America/New_York")
AGENT_ID_FILE = Path("/app/.agent_id")
SESSION_TIMEOUT = 900  # 15 minutes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def get_agent() -> LettaTraderAgent:
    if not AGENT_ID_FILE.exists():
        raise RuntimeError("No agent ID found. Run bootstrap first: python -m scheduler.bootstrap")
    return LettaTraderAgent(agent_id=AGENT_ID_FILE.read_text().strip())


def _get_open_positions() -> list:
    """Fetch current open positions from Alpaca for health check injection."""
    try:
        import requests
        resp = requests.get(
            f"{os.environ['ALPACA_BASE_URL']}/v2/positions",
            headers={
                "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
            },
            timeout=10,
        )
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def _get_todays_trades() -> list:
    """Fetch today's closed/filled orders from Alpaca for EOD injection."""
    try:
        import requests
        today = date.today().isoformat()
        resp = requests.get(
            f"{os.environ['ALPACA_BASE_URL']}/v2/orders",
            headers={
                "APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
                "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
            },
            params={"status": "filled", "after": f"{today}T00:00:00Z", "limit": 50},
            timeout=10,
        )
        return resp.json() if resp.status_code == 200 else []
    except Exception:
        return []


def run_session(session_type: str, prompt: str, max_retries: int = 1):
    """Dispatch a session to the Letta agent with error handling and Telegram notifications."""
    for attempt in range(max_retries + 1):
        try:
            log.info(f"Starting session: {session_type} (attempt {attempt + 1})")
            agent = get_agent()
            raw_output = agent.send_session(prompt)

            # Parse structured output for notifications
            output = parse_session_output(raw_output)

            # Fire notifications based on session type
            if session_type == "market_open" and output.get("trades"):
                for trade in output["trades"]:
                    send_telegram(format_trade_notification(trade))

            elif session_type == "health_check" and output.get("alerts"):
                for alert in output["alerts"]:
                    send_telegram(format_alert(alert))

            elif session_type == "eod_reflection" and output:
                send_telegram(format_eod_summary(output))

            elif session_type == "weekly_review" and output:
                send_telegram(f"📅 WEEKLY REVIEW COMPLETE\n{output.get('summary', '')}")

            # Log session output
            log_path = Path(f"/app/logs/sessions/{date.today().isoformat()}_{session_type}.json")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps({"prompt": prompt, "output": raw_output, "parsed": output}, indent=2))

            log.info(f"Session {session_type} complete.")
            return

        except Exception as e:
            log.error(f"Session {session_type} failed (attempt {attempt + 1}): {e}")
            if attempt < max_retries:
                log.info(f"Retrying in 60s...")
                time.sleep(60)
            else:
                send_telegram(format_error_notification(session_type, str(e)))


# --- Session jobs ---

def job_pre_market():
    now = datetime.now(ET)
    prompt = build_pre_market_prompt(
        date=now.strftime("%Y-%m-%d"),
        market_opens_in="3h30m",
    )
    run_session("pre_market", prompt)


def job_market_open():
    now = datetime.now(ET)
    prompt = build_market_open_prompt(date=now.strftime("%Y-%m-%d"), time_et="09:30")
    run_session("market_open", prompt)


def job_health_check():
    now = datetime.now(ET)
    positions = _get_open_positions()
    prompt = build_health_check_prompt(date=now.strftime("%Y-%m-%d"), positions=positions)
    run_session("health_check", prompt)


def job_eod_reflection():
    now = datetime.now(ET)
    trades = _get_todays_trades()
    prompt = build_eod_reflection_prompt(date=now.strftime("%Y-%m-%d"), trades_today=trades)
    run_session("eod_reflection", prompt)


def job_weekly_review():
    now = datetime.now(ET)
    week_num = now.isocalendar()[1]
    prompt = build_weekly_review_prompt(date=now.strftime("%Y-%m-%d"), week_number=week_num)
    run_session("weekly_review", prompt)


def main():
    scheduler = BlockingScheduler(timezone=ET)

    # Pre-market: Mon-Fri 6:00 AM ET
    scheduler.add_job(job_pre_market, CronTrigger(day_of_week="mon-fri", hour=6, minute=0, timezone=ET))
    # Market open: Mon-Fri 9:30 AM ET
    scheduler.add_job(job_market_open, CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET))
    # Health check: Mon-Fri 1:00 PM ET
    scheduler.add_job(job_health_check, CronTrigger(day_of_week="mon-fri", hour=13, minute=0, timezone=ET))
    # EOD reflection: Mon-Fri 3:45 PM ET
    scheduler.add_job(job_eod_reflection, CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=ET))
    # Weekly review: Sunday 6:00 PM ET
    scheduler.add_job(job_weekly_review, CronTrigger(day_of_week="sun", hour=18, minute=0, timezone=ET))

    log.info("ClaudeTrading scheduler started. 5 jobs scheduled.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify imports resolve**

```bash
python -c "from scheduler.main import main; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add scheduler/main.py
git commit -m "feat: APScheduler main — 5 cron triggers, session dispatch, error recovery"
```

---

## Phase 6: Integration and Deployment

### Task 13: End-to-end dry run (paper account smoke test)

- [ ] **Step 1: Create .env from .env.example and fill in real keys**

```bash
cp .env.example .env
# Edit .env with your actual keys:
# ALPACA_API_KEY, ALPACA_SECRET_KEY, FMP_API_KEY, SERPER_API_KEY,
# TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ANTHROPIC_API_KEY
```

- [ ] **Step 2: Start Docker services**

```bash
docker compose up letta alpaca-mcp -d
```
Expected: Both containers running. Verify with:
```bash
docker compose ps
```
Expected: letta → Up, alpaca-mcp → Up

- [ ] **Step 3: Wait for Letta to be ready**

```bash
curl http://localhost:8283/health
```
Expected: `{"status": "ok"}` or similar health response

- [ ] **Step 4: Run bootstrap**

```bash
docker compose run --rm scheduler python -m scheduler.bootstrap
```
Expected output:
```
Creating Letta agent 'claude_trader'...
Agent created: <uuid>
Registering: ['fmp_screener', 'fmp_ohlcv', 'fmp_news', 'fmp_earnings_calendar', 'serper_search', 'run_script']
Alpaca MCP attached: True
Indicator library loaded.
Bootstrap complete. Agent ID saved to /app/.agent_id
```

- [ ] **Step 5: Trigger a manual pre-market session**

```bash
docker compose run --rm scheduler python -c "
from scheduler.main import job_pre_market
job_pre_market()
print('Pre-market session complete')
"
```
Expected: Session runs, Letta responds, session log written to `/app/logs/sessions/`

- [ ] **Step 6: Check session log**

```bash
cat logs/sessions/$(date +%Y-%m-%d)_pre_market.json | head -50
```
Expected: JSON with prompt and Claude's output

- [ ] **Step 7: Verify Telegram notification was sent**
Check your Telegram chat for a message. If not received, verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`.

- [ ] **Step 8: Start full scheduler**

```bash
docker compose up -d
```
Expected: All 3 services running (letta, alpaca-mcp, scheduler)

- [ ] **Step 9: Commit final state**

```bash
git add .
git commit -m "chore: integration verified, system ready for deployment"
```

---

### Task 14: VPS deployment

- [ ] **Step 1: On your local machine, copy project to VPS**

```bash
rsync -avz --exclude '.env' --exclude 'logs/' --exclude 'letta-db/' \
  /path/to/claudetrading/ user@YOUR_VPS_IP:/opt/claudetrading/
```

- [ ] **Step 2: SSH into VPS and create .env**

```bash
ssh user@YOUR_VPS_IP
cd /opt/claudetrading
cp .env.example .env
nano .env  # fill in all keys
```

- [ ] **Step 3: Install Docker on VPS (if needed)**

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

- [ ] **Step 4: Run bootstrap on VPS**

```bash
cd /opt/claudetrading
docker compose up letta alpaca-mcp -d
sleep 10
docker compose run --rm scheduler python -m scheduler.bootstrap
```
Expected: Same bootstrap output as local test

- [ ] **Step 5: Start all services**

```bash
docker compose up -d
docker compose ps
```
Expected: All 3 services Up

- [ ] **Step 6: Verify logs are being written**

```bash
docker compose logs scheduler -f
```
Expected: "ClaudeTrading scheduler started. 5 jobs scheduled."

- [ ] **Step 7: Final commit tag**

```bash
git tag v1.0.0
git push origin main --tags  # if remote repo configured
```

---

## Full Test Suite

Run all tests at any point:

```bash
pytest tests/ -v --tb=short
```

Expected: All tests PASSED. Minimum coverage targets:
- `scheduler/tools/` → 90%+
- `scheduler/sessions.py` → 100%
- `scheduler/notifier.py` → 85%+
- `scripts/indicators/` → 80%+

---

## API Keys Needed Before Starting

| Key | Where to Get |
|-----|-------------|
| `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | alpaca.markets → Paper Trading account |
| `FMP_API_KEY` | financialmodelingprep.com |
| `SERPER_API_KEY` | serper.dev |
| `TELEGRAM_BOT_TOKEN` | Telegram BotFather (`/newbot`) |
| `TELEGRAM_CHAT_ID` | Send a message to your bot, then: `https://api.telegram.org/bot<TOKEN>/getUpdates` |
| `ANTHROPIC_API_KEY` | console.anthropic.com |

ALPACA_API_KEY = your_alpaca_api_key_here
ALPACA_SECRET_KEY = your_alpaca_secret_key_here
FMP_API_KEY = your_fmp_api_key_here
TELEGRAM_BOT_TOKEN = your_telegram_bot_token_here
TELEGRAM_CHAT_ID = your_telegram_chat_id_here
ANTHROPIC_API_KEY = your_anthropic_api_key_here