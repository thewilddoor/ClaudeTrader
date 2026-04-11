#!/usr/bin/env python3
# Input: JSON list of OHLCV dicts via stdin
# Args: --fast (12), --slow (26), --signal (9)
# Output: {"macd": float, "signal": float, "histogram": float, "crossover": "bullish"|"bearish"|"none"}

import sys
import json
import argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--fast", type=int, default=12)
parser.add_argument("--slow", type=int, default=26)
parser.add_argument("--signal", type=int, default=9)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

ema_fast = close.ewm(span=args.fast, adjust=False).mean()
ema_slow = close.ewm(span=args.slow, adjust=False).mean()
macd_line = ema_fast - ema_slow
signal_line = macd_line.ewm(span=args.signal, adjust=False).mean()
histogram = macd_line - signal_line

macd_val = float(macd_line.iloc[-1])
signal_val = float(signal_line.iloc[-1])
hist_val = float(histogram.iloc[-1])
prev_hist = float(histogram.iloc[-2]) if len(histogram) >= 2 else hist_val

crossover = "none"
if prev_hist < 0 and hist_val >= 0:
    crossover = "bullish"
elif prev_hist > 0 and hist_val <= 0:
    crossover = "bearish"

print(json.dumps({
    "macd": round(macd_val, 4),
    "signal": round(signal_val, 4),
    "histogram": round(hist_val, 4),
    "crossover": crossover,
}))
