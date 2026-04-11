import subprocess
import json
import sys


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
