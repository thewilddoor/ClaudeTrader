#!/usr/bin/env python3
# Input: JSON list of OHLCV dicts via stdin
# Args: --period (default 14)
# Output: {"rsi": float, "signal": "overbought"|"oversold"|"neutral"}

import sys
import json
import argparse
import pandas as pd
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=14)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

delta = close.diff()
gain = delta.clip(lower=0)
loss = (-delta).clip(lower=0)
avg_gain = gain.ewm(com=args.period - 1, min_periods=args.period).mean()
avg_loss = loss.ewm(com=args.period - 1, min_periods=args.period).mean()

# Handle division by zero: if avg_loss is 0 and avg_gain > 0, RSI is 100
rs = pd.Series(np.where(avg_loss == 0, float('inf') if avg_gain.iloc[-1] > 0 else 0, avg_gain / avg_loss))
rsi_series = 100 - (100 / (1 + rs))
rsi = float(rsi_series.fillna(100 if avg_gain.iloc[-1] > 0 else 50).iloc[-1])

signal = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
print(json.dumps({"rsi": round(rsi, 2), "signal": signal, "period": args.period}))
