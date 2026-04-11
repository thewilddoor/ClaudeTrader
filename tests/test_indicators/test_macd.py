import subprocess
import json
import sys

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
