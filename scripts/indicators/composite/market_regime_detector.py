#!/usr/bin/env python3
# scripts/indicators/composite/market_regime_detector.py
# Input: JSON {"spy": [ohlcv...], "vix": [ohlcv...]} via stdin
# Output: {"regime": str, "spy_trend": "up"|"down"|"sideways", "vix_current": float, "vix_percentile_52w": float, "description": str}

import sys
import json
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
