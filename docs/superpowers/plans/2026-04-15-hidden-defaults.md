# Hidden Defaults & System Constraints Visibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make all overridable tool defaults visible to Claude via docstrings, increase API/script timeouts to practical values, and add a System Constraints section to strategy_doc.

**Architecture:** Pure documentation + constant changes across 5 tool files and agent.py. No logic changes. A one-time script patches the live agent's strategy_doc on the VPS after deploy.

**Tech Stack:** Python, pytest, Letta memory blocks

---

## Files Modified

| File | Change |
|------|--------|
| `scheduler/tools/fmp.py` | `timeout=10→30` in 4 calls; add `(changeable)` to 6 param descriptions |
| `scheduler/tools/alpaca.py` | `timeout=10→30` in 5 calls; add `(changeable)` to `list_orders` limit description |
| `scheduler/tools/serper.py` | `timeout=10→30`; add `(changeable)` to `num` description |
| `scheduler/tools/pyexec.py` | `timeout` default `30→60`; memory `256MB→512MB`; docstring update |
| `scheduler/agent.py` | Update Hard Limits line in `INITIAL_STRATEGY_DOC`; append `## System Constraints` section |
| `scripts/one_time/patch_strategy_constraints.py` | New: patches live agent's strategy_doc on VPS |
| `tests/test_tool_defaults.py` | New: verifies timeout values and `(changeable)` text |

---

### Task 1: Tests for timeout values

**Files:**
- Create: `tests/test_tool_defaults.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tool_defaults.py
import ast
import pathlib


def _get_timeout_values(filepath: str) -> list[int]:
    """Parse a Python file and return all timeout= keyword argument values."""
    src = pathlib.Path(filepath).read_text()
    tree = ast.parse(src)
    timeouts = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
                    timeouts.append(kw.value.value)
    return timeouts


def test_fmp_timeouts_are_30():
    values = _get_timeout_values("scheduler/tools/fmp.py")
    assert values, "No timeout= calls found in fmp.py"
    assert all(v == 30 for v in values), f"Expected all 30, got {values}"


def test_alpaca_timeouts_are_30():
    values = _get_timeout_values("scheduler/tools/alpaca.py")
    assert values, "No timeout= calls found in alpaca.py"
    assert all(v == 30 for v in values), f"Expected all 30, got {values}"


def test_serper_timeout_is_30():
    values = _get_timeout_values("scheduler/tools/serper.py")
    assert values, "No timeout= calls found in serper.py"
    assert all(v == 30 for v in values), f"Expected all 30, got {values}"


def test_run_script_default_timeout_is_60():
    src = pathlib.Path("scheduler/tools/pyexec.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_script":
            for arg, default in zip(
                reversed(node.args.args), reversed(node.args.defaults)
            ):
                if arg.arg == "timeout":
                    assert isinstance(default, ast.Constant) and default.value == 60, \
                        f"run_script timeout default should be 60, got {default.value}"
                    return
    raise AssertionError("run_script timeout default not found")


def test_run_script_memory_limit_is_512mb():
    src = pathlib.Path("scheduler/tools/pyexec.py").read_text()
    assert "512 * 1024 * 1024" in src, "Expected 512MB memory limit"
    assert "256 * 1024 * 1024" not in src, "Old 256MB limit still present"


def test_fmp_screener_docstring_has_changeable():
    src = pathlib.Path("scheduler/tools/fmp.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "fmp_screener":
            docstring = ast.get_docstring(node) or ""
            assert docstring.count("changeable") >= 4, \
                f"fmp_screener docstring should have 4+ 'changeable', found: {docstring.count('changeable')}"
            return
    raise AssertionError("fmp_screener not found")


def test_strategy_doc_has_system_constraints():
    src = pathlib.Path("scheduler/agent.py").read_text()
    assert "## System Constraints" in src, "strategy_doc missing ## System Constraints section"
    assert "30s" in src, "System Constraints should mention 30s API timeout"
    assert "60s/512MB" in src, "System Constraints should mention 60s/512MB run_script limits"
    assert "60 days" in src, "System Constraints should mention 60-day backtest window"
```

- [ ] **Step 2: Run tests — verify they all fail**

```bash
cd /Users/ziyao_bai/Desktop/ClaudeTrading
pytest tests/test_tool_defaults.py -v
```

Expected: all 7 tests FAIL (timeouts still 10, memory still 256MB, no system constraints)

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/test_tool_defaults.py
git commit -m "test: add failing tests for timeout values and system constraints visibility"
```

---

### Task 2: Update `fmp.py` — timeouts and docstrings

**Files:**
- Modify: `scheduler/tools/fmp.py`

- [ ] **Step 1: Update all 4 timeout values and add `(changeable)` to docstrings**

Replace the `fmp_screener` docstring Args block (lines 20–28):

```python
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
```

Replace the `fmp_ohlcv` docstring `limit` arg line:
```
        limit: Number of trading days of history to return (default 90, changeable).
```

Replace the `fmp_news` docstring `limit` arg line:
```
        limit: Maximum number of news articles to return per ticker (default 10, changeable).
```

Replace all 4 `timeout=10` with `timeout=30`:
- Line 41: `response = requests.get("https://financialmodelingprep.com/stable/company-screener", params=params, timeout=30)`
- Line 62: `response = requests.get("https://financialmodelingprep.com/stable/historical-price-eod/full", params=params, timeout=30)`
- Line 86: `response = requests.get("https://financialmodelingprep.com/stable/news/stock", params=params, timeout=30)`
- Line 107: `response = requests.get("https://financialmodelingprep.com/stable/earnings-calendar", params=params, timeout=30)`

- [ ] **Step 2: Run targeted tests**

```bash
pytest tests/test_tool_defaults.py::test_fmp_timeouts_are_30 tests/test_tool_defaults.py::test_fmp_screener_docstring_has_changeable -v
```

Expected: both PASS

- [ ] **Step 3: Commit**

```bash
git add scheduler/tools/fmp.py
git commit -m "feat: increase FMP timeouts to 30s and mark defaults as changeable"
```

---

### Task 3: Update `alpaca.py` — timeouts and docstring

**Files:**
- Modify: `scheduler/tools/alpaca.py`

- [ ] **Step 1: Replace all 5 `timeout=10` with `timeout=30`**

Lines 35, 63, 117, 153, 184 — change `timeout=10` to `timeout=30` in each.

- [ ] **Step 2: Update `alpaca_list_orders` `limit` arg description**

```python
        limit: Maximum number of orders to return (default 50, changeable).
```

- [ ] **Step 3: Run targeted test**

```bash
pytest tests/test_tool_defaults.py::test_alpaca_timeouts_are_30 -v
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add scheduler/tools/alpaca.py
git commit -m "feat: increase Alpaca timeouts to 30s and mark list_orders limit as changeable"
```

---

### Task 4: Update `serper.py` — timeout and docstring

**Files:**
- Modify: `scheduler/tools/serper.py`

- [ ] **Step 1: Replace `timeout=10` with `timeout=30` and update `num` docstring**

```python
def serper_search(
    query: str,
    search_type: str = "search",
    num: int = 10,
    api_key: Optional[str] = None,
) -> dict:
    """Search the web via Serper (Google Search API).

    Args:
        query: Search query string (e.g. 'AAPL earnings report Q1 2026').
        search_type: Type of search; 'search' for general web results, 'news' for news.
        num: Number of results to return (default 10, changeable).
        api_key: Serper API key; reads from SERPER_API_KEY env var if not provided.

    Returns:
        dict: Search results with 'organic' list (search) or 'news' list (news type).
    """
```

And on the `requests.post` call: `timeout=30`

- [ ] **Step 2: Run targeted test**

```bash
pytest tests/test_tool_defaults.py::test_serper_timeout_is_30 -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add scheduler/tools/serper.py
git commit -m "feat: increase Serper timeout to 30s and mark num as changeable"
```

---

### Task 5: Update `pyexec.py` — memory limit, timeout default, docstring

**Files:**
- Modify: `scheduler/tools/pyexec.py`

- [ ] **Step 1: Update memory limit, timeout default, and docstring**

In `_set_resource_limits` (line 18), change:
```python
resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
```

In `run_script` signature (line 25), change:
```python
def run_script(
    code: str,
    timeout: int = 60,
    scripts_dir: Optional[str] = None,
) -> dict:
```

Update the `timeout` docstring arg line:
```
        timeout: Maximum execution time in seconds before the process is killed (default 60, changeable).
```

- [ ] **Step 2: Run targeted tests**

```bash
pytest tests/test_tool_defaults.py::test_run_script_default_timeout_is_60 tests/test_tool_defaults.py::test_run_script_memory_limit_is_512mb -v
```

Expected: both PASS

- [ ] **Step 3: Commit**

```bash
git add scheduler/tools/pyexec.py
git commit -m "feat: increase run_script timeout to 60s and memory limit to 512MB"
```

---

### Task 6: Update `agent.py` — strategy_doc System Constraints

**Files:**
- Modify: `scheduler/agent.py`

- [ ] **Step 1: Update Hard Limits line and append System Constraints section**

In `INITIAL_STRATEGY_DOC`, find the Hard Limits section and update the `run_script` line:

```
- run_script: sandboxed — no credentials injected, 512MB RAM, 60s timeout.
```

Then append the following to the end of `INITIAL_STRATEGY_DOC` (before the closing `"""`):

```
## System Constraints
All tool defaults are starting points — pass explicit values to override.
Hard limits (not overridable): API calls timeout at 30s; run_script kills at 60s/512MB;
strategy backtest window is 60 days; only one probationary strategy at a time.
```

- [ ] **Step 2: Run targeted test**

```bash
pytest tests/test_tool_defaults.py::test_strategy_doc_has_system_constraints -v
```

Expected: PASS

- [ ] **Step 3: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add scheduler/agent.py
git commit -m "feat: add System Constraints to strategy_doc and update hard limits values"
```

---

### Task 7: One-time script to patch live agent's strategy_doc

**Files:**
- Create: `scripts/one_time/patch_strategy_constraints.py`

This script reads the live agent's current strategy_doc, appends the System Constraints section if missing, and writes it back via Letta.

- [ ] **Step 1: Create the script**

```python
"""
One-time script: patch live agent's strategy_doc to add System Constraints section
and update the Hard Limits run_script line.

Run on VPS:
  docker-compose exec scheduler python scripts/one_time/patch_strategy_constraints.py
"""
import os
import sys

sys.path.insert(0, "/app")

from scheduler.agent import LettaTraderAgent

SYSTEM_CONSTRAINTS = """
## System Constraints
All tool defaults are starting points — pass explicit values to override.
Hard limits (not overridable): API calls timeout at 30s; run_script kills at 60s/512MB;
strategy backtest window is 60 days; only one probationary strategy at a time."""

OLD_HARD_LIMIT_LINE = "- run_script: sandboxed — no credentials injected, 256MB RAM, 30s timeout."
NEW_HARD_LIMIT_LINE = "- run_script: sandboxed — no credentials injected, 512MB RAM, 60s timeout."


def main():
    state_path = os.environ.get("AGENT_STATE_PATH", "/app/state/.agent_id")
    with open(state_path) as f:
        agent_id = f.read().strip()

    agent = LettaTraderAgent(agent_id=agent_id)
    current = agent.get_memory_block("strategy_doc")

    if current is None:
        print("ERROR: strategy_doc block not found")
        sys.exit(1)

    updated = current

    # Patch hard limits line
    if OLD_HARD_LIMIT_LINE in updated:
        updated = updated.replace(OLD_HARD_LIMIT_LINE, NEW_HARD_LIMIT_LINE)
        print("Patched: run_script hard limits line updated to 512MB/60s")
    else:
        print("INFO: Hard limits line already updated or not found — skipping")

    # Append system constraints if not already present
    if "## System Constraints" in updated:
        print("INFO: ## System Constraints already present — skipping append")
    else:
        updated = updated.rstrip() + SYSTEM_CONSTRAINTS + "\n"
        print("Appended: ## System Constraints section added")

    if updated == current:
        print("No changes needed.")
        return

    agent.update_memory_block("strategy_doc", updated)
    print("Done: strategy_doc patched successfully.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add scripts/one_time/patch_strategy_constraints.py
git commit -m "feat: add one-time script to patch live agent strategy_doc with system constraints"
```

---

### Task 8: Final verification

- [ ] **Step 1: Run full test suite**

```bash
pytest -v
```

Expected: all tests pass including the 7 new ones in `test_tool_defaults.py`

- [ ] **Step 2: Check strategy_doc character count stays under 5500**

```bash
python -c "
from scheduler.agent import INITIAL_STRATEGY_DOC
print(f'strategy_doc length: {len(INITIAL_STRATEGY_DOC)} / 5500 chars')
assert len(INITIAL_STRATEGY_DOC) <= 5500, 'OVER LIMIT'
print('OK')
"
```

Expected: prints length under 5500

- [ ] **Step 3: Final commit and push**

```bash
git push origin main
```
