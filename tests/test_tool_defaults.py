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


def test_strategy_doc_has_system_constraints():
    src = pathlib.Path("scheduler/agent.py").read_text()
    assert "## System Constraints" in src, "strategy_doc missing ## System Constraints section"
    assert "30s" in src, "System Constraints should mention 30s API timeout"
    assert "60s/512MB" in src, "System Constraints should mention 60s/512MB run_script limits"
    assert "60 days" in src, "System Constraints should mention 60-day backtest window"
