#!/usr/bin/env python3
# scripts/indicators/volume/volume_profile.py
# Input: JSON OHLCV via stdin | Args: --bins (10)
# Output: {"poc": float, "value_area_high": float, "value_area_low": float, "high_volume_nodes": [float]}

import sys
import json
import argparse
import pandas as pd
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--bins", type=int, default=10)
args = parser.parse_args()

data = json.load(sys.stdin)
df = pd.DataFrame(data).sort_values("date")
prices = df["close"].astype(float)
volumes = df["volume"].astype(float)

price_min, price_max = prices.min(), prices.max()
bin_edges = np.linspace(price_min, price_max, args.bins + 1)
bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

vol_profile = np.zeros(args.bins)
for price, vol in zip(prices, volumes):
    idx = min(int((price - price_min) / (price_max - price_min + 1e-9) * args.bins), args.bins - 1)
    vol_profile[idx] += vol

poc_idx = int(np.argmax(vol_profile))
poc = round(float(bin_centers[poc_idx]), 2)
total_vol = vol_profile.sum()
va_vol = total_vol * 0.7
sorted_idx = np.argsort(vol_profile)[::-1]
va_indices, acc = [], 0
for i in sorted_idx:
    if acc >= va_vol:
        break
    va_indices.append(i)
    acc += vol_profile[i]

va_prices = [bin_centers[i] for i in va_indices]
hvn = [round(float(bin_centers[i]), 2) for i in sorted_idx[:3]]
print(json.dumps({
    "poc": poc,
    "value_area_high": round(float(max(va_prices)), 2),
    "value_area_low": round(float(min(va_prices)), 2),
    "high_volume_nodes": hvn,
}))
