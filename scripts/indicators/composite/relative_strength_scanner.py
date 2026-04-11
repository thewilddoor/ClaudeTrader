#!/usr/bin/env python3
# scripts/indicators/composite/relative_strength_scanner.py
# Input: JSON {"TICKER": [{"date": ..., "close": ...}, ...], ...} via stdin
# Output: {"ranked": [{"ticker": str, "rs_score": float, "return_pct": float}, ...], "top_picks": [str]}

import sys
import json
import pandas as pd

data = json.load(sys.stdin)
scores = []

for ticker, ohlcv in data.items():
    df = pd.DataFrame(ohlcv).sort_values("date")
    close = df["close"].astype(float)
    if len(close) < 2:
        continue
    ret_pct = (close.iloc[-1] - close.iloc[0]) / close.iloc[0] * 100
    split = max(1, len(close) * 3 // 4)
    recent_ret = (close.iloc[-1] - close.iloc[split]) / close.iloc[split] * 100
    rs_score = round(ret_pct * 0.4 + recent_ret * 0.6, 2)
    scores.append({"ticker": ticker, "rs_score": rs_score, "return_pct": round(ret_pct, 2)})

ranked = sorted(scores, key=lambda x: x["rs_score"], reverse=True)
top_picks = [r["ticker"] for r in ranked[:5]]

print(json.dumps({"ranked": ranked, "top_picks": top_picks}))
