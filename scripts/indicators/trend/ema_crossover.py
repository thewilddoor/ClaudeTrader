#!/usr/bin/env python3
# Input: JSON list of OHLCV dicts via stdin
# Args: --fast (default 9), --slow (default 21)
# Output: {"fast_ema": float, "slow_ema": float, "signal": str, "crossover_today": bool}

import sys
import json
import argparse
import pandas as pd

parser = argparse.ArgumentParser()
parser.add_argument("--fast", type=int, default=9)
parser.add_argument("--slow", type=int, default=21)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)

fast = close.ewm(span=args.fast, adjust=False).mean()
slow = close.ewm(span=args.slow, adjust=False).mean()

fast_now, slow_now = float(fast.iloc[-1]), float(slow.iloc[-1])
fast_prev, slow_prev = float(fast.iloc[-2]), float(slow.iloc[-2])

crossover_today = (fast_prev <= slow_prev and fast_now > slow_now) or (fast_prev >= slow_prev and fast_now < slow_now)
signal = "bullish" if fast_now > slow_now else "bearish" if fast_now < slow_now else "neutral"

print(json.dumps({"fast_ema": round(fast_now, 4), "slow_ema": round(slow_now, 4), "signal": signal, "crossover_today": crossover_today}))
