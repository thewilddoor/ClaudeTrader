#!/usr/bin/env python3
# scripts/indicators/trend/supertrend.py
# Input: JSON OHLCV via stdin | Args: --period (10), --multiplier (3.0)
# Output: {"supertrend": float, "signal": "buy"|"sell", "trend": "up"|"down"}

import sys
import json
import argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=10)
parser.add_argument("--multiplier", type=float, default=3.0)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date").reset_index(drop=True)
high = df["high"].astype(float)
low = df["low"].astype(float)
close = df["close"].astype(float)

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
