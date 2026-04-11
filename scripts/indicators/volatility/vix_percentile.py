#!/usr/bin/env python3
# scripts/indicators/volatility/vix_percentile.py
# Input: JSON list of VIX close values {"date": ..., "close": ...} via stdin
# Output: {"vix_current": float, "percentile_52w": float, "regime": "low"|"normal"|"elevated"|"extreme"}

import sys
import json
import pandas as pd

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)
vix = float(close.iloc[-1])
window = close.tail(252) if len(close) >= 252 else close
pct = round(float((window <= vix).mean() * 100), 1)
regime = "extreme" if vix > 40 else "elevated" if vix > 25 else "normal" if vix > 15 else "low"
print(json.dumps({"vix_current": round(vix, 2), "percentile_52w": pct, "regime": regime}))
