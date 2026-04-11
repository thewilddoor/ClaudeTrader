import subprocess
import json
import sys


def run_indicator(script_path, data, **kwargs):
    args = [sys.executable, script_path]
    for k, v in kwargs.items():
        args += [f"--{k}", str(v)]
    result = subprocess.run(args, input=json.dumps(data), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_market_regime_detector_returns_regime():
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
