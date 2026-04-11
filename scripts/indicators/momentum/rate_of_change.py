#!/usr/bin/env python3
# scripts/indicators/momentum/rate_of_change.py
# Input: JSON OHLCV via stdin | Args: --period (10)
# Output: {"roc": float, "signal": "accelerating"|"decelerating"|"flat"}

import sys
import json
import argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=10)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

roc = float(((close.iloc[-1] - close.iloc[-args.period]) / close.iloc[-args.period]) * 100)
prev_roc = float(((close.iloc[-2] - close.iloc[-args.period - 1]) / close.iloc[-args.period - 1]) * 100) if len(close) > args.period + 1 else roc
signal = "accelerating" if roc > prev_roc else "decelerating" if roc < prev_roc else "flat"
print(json.dumps({"roc": round(roc, 2), "prev_roc": round(prev_roc, 2), "signal": signal}))
