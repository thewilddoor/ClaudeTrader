import subprocess
import json
import sys


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
