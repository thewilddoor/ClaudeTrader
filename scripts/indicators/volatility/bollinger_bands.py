#!/usr/bin/env python3
# Input: JSON list of OHLCV dicts via stdin
# Args: --period (default 20), --std (default 2.0)
# Output: {"upper": float, "middle": float, "lower": float, "bandwidth": float, "position": str}

import sys
import json
import argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--period", type=int, default=20)
parser.add_argument("--std", type=float, default=2.0)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

middle = close.rolling(args.period).mean()
std = close.rolling(args.period).std()
upper = middle + args.std * std
lower = middle - args.std * std

upper_val = round(float(upper.iloc[-1]), 4)
middle_val = round(float(middle.iloc[-1]), 4)
lower_val = round(float(lower.iloc[-1]), 4)
current = float(close.iloc[-1])
bandwidth = round((upper_val - lower_val) / middle_val * 100, 2)

position = "above_upper" if current > upper_val else "below_lower" if current < lower_val else "inside"
print(json.dumps({"upper": upper_val, "middle": middle_val, "lower": lower_val, "bandwidth": bandwidth, "position": position}))
