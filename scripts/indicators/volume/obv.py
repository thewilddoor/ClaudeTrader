#!/usr/bin/env python3
# scripts/indicators/volume/obv.py
# Input: JSON OHLCV via stdin
# Output: {"obv": float, "obv_trend": "rising"|"falling"|"flat", "obv_divergence": bool}

import sys
import json
import pandas as pd

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
close = df["close"].astype(float)
volume = df["volume"].astype(float)
direction = close.diff().apply(lambda x: 1 if x > 0 else -1 if x < 0 else 0)
obv = (direction * volume).cumsum()
obv_now = float(obv.iloc[-1])
obv_5d_ago = float(obv.iloc[-6]) if len(obv) > 5 else float(obv.iloc[0])
obv_trend = "rising" if obv_now > obv_5d_ago else "falling" if obv_now < obv_5d_ago else "flat"
price_up = close.iloc[-1] > close.iloc[-6] if len(close) > 5 else True
obv_divergence = (price_up and obv_trend == "falling") or (not price_up and obv_trend == "rising")
print(json.dumps({"obv": round(obv_now, 0), "obv_trend": obv_trend, "obv_divergence": obv_divergence}))
