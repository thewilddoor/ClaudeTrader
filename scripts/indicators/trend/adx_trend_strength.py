#!/usr/bin/env python3
# scripts/indicators/trend/adx_trend_strength.py
# Input: JSON OHLCV list via stdin | Args: --period (14)
# Output: {"adx": float, "trend_strength": "strong"|"moderate"|"weak", "di_plus": float, "di_minus": float}

import sys
import json
import argparse
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
dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, 1)
adx = float(dx.ewm(com=args.period - 1, min_periods=args.period).mean().iloc[-1])

strength = "strong" if adx > 25 else "moderate" if adx > 20 else "weak"
print(json.dumps({
    "adx": round(adx, 2),
    "trend_strength": strength,
    "di_plus": round(float(di_plus.iloc[-1]), 2),
    "di_minus": round(float(di_minus.iloc[-1]), 2),
}))
