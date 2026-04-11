#!/usr/bin/env python3
# scripts/indicators/volume/vwap.py
# Input: JSON OHLCV list via stdin
# Output: {"vwap": float, "position": "above"|"below", "deviation_pct": float}

import sys
import json
import pandas as pd

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
typical_price = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3
volume = df["volume"].astype(float)
vwap = float((typical_price * volume).sum() / volume.sum())
current = float(df["close"].astype(float).iloc[-1])
deviation_pct = round((current - vwap) / vwap * 100, 2)
position = "above" if current > vwap else "below"
print(json.dumps({"vwap": round(vwap, 4), "position": position, "deviation_pct": deviation_pct}))
