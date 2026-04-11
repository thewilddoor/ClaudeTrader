#!/usr/bin/env python3
# Input: JSON list of OHLCV dicts via stdin
# Args: --period (default 14)
# Output: {"atr": float, "atr_pct": float, "period": int}

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

prev_close = close.shift(1)
tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
atr = float(tr.ewm(com=args.period - 1, min_periods=args.period).mean().iloc[-1])
atr_pct = round(atr / float(close.iloc[-1]) * 100, 2)

print(json.dumps({"atr": round(atr, 4), "atr_pct": atr_pct, "period": args.period}))
