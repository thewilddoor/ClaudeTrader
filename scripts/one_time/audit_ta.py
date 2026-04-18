"""
Mathematical audit of scheduler/tools/_ta.py.

Tests:
1. resample_weekly  — OHLCV aggregation rules, ISO-week anchoring, partial week
2. calc_vwap        — Monday anchor detection, VWAP formula, anchor_idx=0 edge case
3. calc_ema_samples — correct sample indices, correct EMA values
4. calc_adx         — trend_strength classification thresholds

Run from repo root:
    source .venv/bin/activate
    python scripts/one_time/audit_ta.py
"""

import sys
import math
import numpy as np
from datetime import date, timedelta

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
results = []


def check(name: str, expected, actual, tol: float = 0.0, exact: bool = False):
    if exact:
        ok = expected == actual
    elif tol == 0.0:
        ok = expected == actual
    else:
        try:
            ok = abs(expected - actual) <= tol
        except TypeError:
            ok = expected == actual
    status = PASS if ok else FAIL
    results.append((status, name, expected, actual))
    marker = "PASS" if ok else "FAIL"
    print(f"  [{marker}] {name}")
    if not ok:
        print(f"        expected : {expected!r}")
        print(f"        actual   : {actual!r}")
    return ok


def section(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


# ─────────────────────────────────────────────────────────────────────────────
# Import the module under test
# ─────────────────────────────────────────────────────────────────────────────

sys.path.insert(0, "/Users/ziyao_bai/Desktop/ClaudeTrading")
from scheduler.tools._ta import (
    resample_weekly,
    calc_vwap,
    calc_ema_samples,
    calc_adx,
)
import talib  # needed for reference EMA

# ─────────────────────────────────────────────────────────────────────────────
# 1. resample_weekly
# ─────────────────────────────────────────────────────────────────────────────

section("1. resample_weekly — OHLCV aggregation and ISO week anchoring")

# Build 10 bars spanning 3 genuine ISO weeks:
#   Week A: Mon 2026-03-30 ... Fri 2026-04-03 (5 bars, complete)
#   Week B: Mon 2026-04-06 ... Thu 2026-04-09 (4 bars, NOT ending on Friday)
#   Week C: Mon 2026-04-13 only              (1 bar, partial current week)
#
# Key: 2026-04-17 (Friday) is in the SAME ISO week as Mon 2026-04-13.
# A genuine new/partial week starts on Monday 2026-04-13 if we only have
# data up to that Monday. We confirm the partial week is included as a
# weekly bar anchored to its Monday.

week_a_dates = ["2026-03-30","2026-03-31","2026-04-01","2026-04-02","2026-04-03"]
week_b_dates = ["2026-04-06","2026-04-07","2026-04-08","2026-04-09"]
week_c_dates = ["2026-04-13"]  # only Monday so far (partial current week)

np.random.seed(42)
n = 10
opens  = np.round(np.random.uniform(100, 200, n), 2)
highs  = opens + np.round(np.random.uniform(0, 10, n), 2)
lows   = opens - np.round(np.random.uniform(0, 10, n), 2)
closes = opens + np.round(np.random.uniform(-5, 5, n), 2)
vols   = np.random.randint(1_000_000, 5_000_000, n).astype(float)

all_dates = week_a_dates + week_b_dates + week_c_dates
assert len(all_dates) == n, f"Expected 10 dates, got {len(all_dates)}"

records = [
    {
        "date":   all_dates[i],
        "open":   float(opens[i]),
        "high":   float(highs[i]),
        "low":    float(lows[i]),
        "close":  float(closes[i]),
        "volume": float(vols[i]),
    }
    for i in range(n)
]

# Manual computation — each week: O=first, H=max, L=min, C=last, V=sum
def manual_weekly(recs, week_indices_list, monday_dates):
    out = []
    for monday, idxs in zip(monday_dates, week_indices_list):
        week_recs = [recs[i] for i in idxs]
        out.append({
            "d": monday,
            "o": float(week_recs[0]["open"]),
            "h": float(max(r["high"] for r in week_recs)),
            "l": float(min(r["low"]  for r in week_recs)),
            "c": float(week_recs[-1]["close"]),
            "v": float(sum(r["volume"] for r in week_recs)),
        })
    return out

expected_weeks = manual_weekly(
    records,
    [list(range(0, 5)), list(range(5, 9)), [9]],
    ["2026-03-30", "2026-04-06", "2026-04-13"],
)

actual_weeks = resample_weekly(records)

check("Number of weekly bars returned == 3", 3, len(actual_weeks), exact=True)

for i, (exp, act) in enumerate(zip(expected_weeks, actual_weeks)):
    label = f"Week {i+1} (monday={exp['d']})"
    check(f"{label} monday (d)", exp["d"], act["d"], exact=True)
    check(f"{label} open (o)",   exp["o"], act["o"],   tol=1e-9)
    check(f"{label} high (h)",   exp["h"], act["h"],   tol=1e-9)
    check(f"{label} low  (l)",   exp["l"], act["l"],   tol=1e-9)
    check(f"{label} close (c)",  exp["c"], act["c"],   tol=1e-9)
    check(f"{label} volume (v)", exp["v"], act["v"],   tol=1e-9)

# Verify partial week (week C: only Monday 2026-04-13) IS included
partial_included = len(actual_weeks) == 3 and actual_weeks[-1]["d"] == "2026-04-13"
check("Partial current week IS included (anchored to its Monday)", True, partial_included, exact=True)

# Verify partial week bar has exactly the single bar's values
if len(actual_weeks) == 3:
    pw = actual_weeks[2]
    single = records[9]
    check("Partial week open  == single bar open",   float(single["open"]),   pw["o"], tol=1e-9)
    check("Partial week high  == single bar high",   float(single["high"]),   pw["h"], tol=1e-9)
    check("Partial week low   == single bar low",    float(single["low"]),    pw["l"], tol=1e-9)
    check("Partial week close == single bar close",  float(single["close"]),  pw["c"], tol=1e-9)
    check("Partial week volume == single bar volume",float(single["volume"]), pw["v"], tol=1e-9)

# Verify ISO week anchoring: each d must be a Monday
for w in actual_weeks:
    d_obj = date.fromisoformat(w["d"])
    check(f"ISO anchor {w['d']} is Monday (weekday=0)", 0, d_obj.weekday(), exact=True)

# Additional: verify a mid-week date maps correctly to its Monday
# 2026-04-17 (Friday) should group with monday 2026-04-13
# 2026-04-08 (Wednesday) should group with monday 2026-04-06
def get_monday(date_str):
    d = date.fromisoformat(date_str)
    return (d - timedelta(days=d.weekday())).isoformat()

check("Friday 2026-04-17 maps to monday 2026-04-13",
      "2026-04-13", get_monday("2026-04-17"), exact=True)
check("Wednesday 2026-04-08 maps to monday 2026-04-06",
      "2026-04-06", get_monday("2026-04-08"), exact=True)
check("Monday 2026-04-13 maps to itself",
      "2026-04-13", get_monday("2026-04-13"), exact=True)

# ─────────────────────────────────────────────────────────────────────────────
# 2. calc_vwap — anchor detection and formula
# ─────────────────────────────────────────────────────────────────────────────

section("2. calc_vwap — Monday anchor, VWAP formula, edge cases")

# Build a 10-bar dataset where we know exactly which index is Monday.
# 2026-04-13 is Monday of current week relative to 2026-04-17.
# Use 5 bars from prev week (Apr 6-10) + 5 bars from current week (Apr 13-17).

vwap_dates = [
    "2026-04-06","2026-04-07","2026-04-08","2026-04-09","2026-04-10",  # prev week
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17",  # current week
]
nv = len(vwap_dates)

np.random.seed(42)
vh = np.round(np.random.uniform(105, 115, nv), 4)
vl = np.round(np.random.uniform(95,  105, nv), 4)
# ensure high >= low
vh = np.maximum(vh, vl + 0.01)
vc = np.round((vh + vl) / 2 + np.random.uniform(-1, 1, nv), 4)
vv = np.round(np.random.uniform(500_000, 2_000_000, nv), 0)

# Manual VWAP calculation
# anchor_idx = first index whose date >= monday of last date (2026-04-17)
# monday of 2026-04-17 is 2026-04-13 -> index 5
anchor_idx_expected = 5
anchor_date_check = vwap_dates[anchor_idx_expected]
check("Anchor date at index 5 is 2026-04-13", "2026-04-13", anchor_date_check, exact=True)

typical = (vh + vl + vc) / 3.0
cum_tp_vol = np.cumsum(typical * vv)
cum_vol    = np.cumsum(vv)

anchor_tp_vol = cum_tp_vol[anchor_idx_expected - 1]  # idx=4 (prev bar before anchor)
anchor_vol    = cum_vol[anchor_idx_expected - 1]

anchored_cum_tp = cum_tp_vol - anchor_tp_vol
anchored_cum_v  = cum_vol    - anchor_vol

safe_v = np.where(anchored_cum_v > 0, anchored_cum_v, 1.0)
manual_vwap_series = np.where(
    anchored_cum_v > 0,
    anchored_cum_tp / safe_v,
    typical,
)
manual_vwap_last = round(float(manual_vwap_series[-1]), 4)

result_vwap = calc_vwap(vh, vl, vc, vv, vwap_dates)

check("VWAP last value matches manual", manual_vwap_last, result_vwap["vwap"], tol=1e-4)

# Verify price_vs_vwap_pct formula
cur_close_v = float(vc[-1])
cur_vwap_v  = float(result_vwap["vwap"])
expected_pct = round((cur_close_v - cur_vwap_v) / cur_vwap_v * 100, 4) if cur_vwap_v != 0 else None
check("price_vs_vwap_pct formula correct", expected_pct, result_vwap["price_vs_vwap_pct"], tol=1e-4)

# Verify all bars in current week individually (indices 5..9)
for i in range(anchor_idx_expected, nv):
    expected_vi = round(float(manual_vwap_series[i]), 4)
    actual_vi   = round(float(result_vwap["vwap_series"][i]), 4)
    check(f"VWAP series bar[{i}] ({vwap_dates[i]})", expected_vi, actual_vi, tol=1e-4)

# Edge case: anchor_idx = 0 (all data is in current week -- no prior bars)
vwap_dates_allweek = [
    "2026-04-13","2026-04-14","2026-04-15","2026-04-16","2026-04-17"
]
naw = len(vwap_dates_allweek)
np.random.seed(99)
awh = np.round(np.random.uniform(105, 115, naw), 4)
awl = np.round(np.random.uniform(95,  105, naw), 4)
awh = np.maximum(awh, awl + 0.01)
awc = np.round((awh + awl) / 2 + np.random.uniform(-1, 1, naw), 4)
awv = np.round(np.random.uniform(500_000, 2_000_000, naw), 0)

# Manual: anchor_idx=0, so anchor_tp_vol=0, anchor_vol=0
aw_typical = (awh + awl + awc) / 3.0
aw_cum_tp = np.cumsum(aw_typical * awv)
aw_cum_v  = np.cumsum(awv)
aw_vwap_series = aw_cum_tp / aw_cum_v
aw_manual_last = round(float(aw_vwap_series[-1]), 4)

result_aw = calc_vwap(awh, awl, awc, awv, vwap_dates_allweek)
check("VWAP anchor_idx=0 edge case: last value correct", aw_manual_last, result_aw["vwap"], tol=1e-4)

# Verify vwap_series[0] == typical[0] when anchor is at 0
aw_vwap_at_0 = round(float(aw_typical[0]), 4)
actual_series_0 = round(float(result_aw["vwap_series"][0]), 4)
check("VWAP anchor_idx=0: vwap_series[0] == typical[0]", aw_vwap_at_0, actual_series_0, tol=1e-4)

# Verify each bar in all-week case
for i in range(naw):
    expected_vi = round(float(aw_vwap_series[i]), 4)
    actual_vi   = round(float(result_aw["vwap_series"][i]), 4)
    check(f"VWAP all-week bar[{i}] ({vwap_dates_allweek[i]})", expected_vi, actual_vi, tol=1e-4)

# ─────────────────────────────────────────────────────────────────────────────
# 3. calc_ema_samples — indices and values
# ─────────────────────────────────────────────────────────────────────────────

section("3. calc_ema_samples — sample indices and EMA values")

# Generate 260-bar synthetic dataset with known dates
# Go back 260 trading days from 2026-04-17
end_date = date(2026, 4, 17)
trading_dates = []
d = end_date
while len(trading_dates) < 260:
    if d.weekday() < 5:  # Monday-Friday
        trading_dates.append(d)
    d -= timedelta(days=1)
trading_dates.reverse()  # oldest first
date_strs = [d.isoformat() for d in trading_dates]

np.random.seed(42)
n260 = len(trading_dates)
ec = 100.0 + np.cumsum(np.random.randn(n260) * 1.5)
ec = np.round(np.maximum(ec, 1.0), 4)

# Reference EMA computed independently (SMA seed + EMA recursion)
def compute_ema_ref(close_arr, period):
    alpha = 2.0 / (period + 1)
    ema = np.full(len(close_arr), np.nan)
    ema[period - 1] = np.mean(close_arr[:period])
    for i in range(period, len(close_arr)):
        ema[i] = alpha * close_arr[i] + (1 - alpha) * ema[i - 1]
    return ema

ref_ema21  = compute_ema_ref(ec, 21)
ref_ema55  = compute_ema_ref(ec, 55)
ref_ema89  = compute_ema_ref(ec, 89)
talib_ema21 = talib.EMA(ec.astype("f8"), timeperiod=21)
talib_ema55 = talib.EMA(ec.astype("f8"), timeperiod=55)
talib_ema89 = talib.EMA(ec.astype("f8"), timeperiod=89)

# Verify reference matches talib
diff_ema21 = float(np.nanmax(np.abs(ref_ema21[21:] - talib_ema21[21:])))
diff_ema55 = float(np.nanmax(np.abs(ref_ema55[55:] - talib_ema55[55:])))
diff_ema89 = float(np.nanmax(np.abs(ref_ema89[89:] - talib_ema89[89:])))
check("Reference EMA(21) matches talib within 1e-6", True, diff_ema21 < 1e-6, exact=True)
check("Reference EMA(55) matches talib within 1e-6", True, diff_ema55 < 1e-6, exact=True)
check("Reference EMA(89) matches talib within 1e-6", True, diff_ema89 < 1e-6, exact=True)

# Expected sample indices
# n=260, sample_every=5, n_samples=5
# raw = list(range(259, max(259-25,-1), -5))[:5] = [259, 254, 249, 244, 239]
# reversed -> [239, 244, 249, 254, 259]
n_e = 260
sample_every = 5
n_samples = 5
raw_indices = list(range(n_e - 1, max(n_e - 1 - sample_every * n_samples, -1), -sample_every))[:n_samples]
raw_indices.reverse()
expected_indices = raw_indices

check("Expected sample indices == [239, 244, 249, 254, 259]",
      [239, 244, 249, 254, 259], expected_indices, exact=True)

# Call calc_ema_samples and verify
result_ema = calc_ema_samples(ec, date_strs, periods=[21, 55, 89])
check("Number of samples returned == 5", 5, len(result_ema["ema_samples"]), exact=True)

for pos, idx in enumerate(expected_indices):
    s = result_ema["ema_samples"][pos]
    # Date check
    check(f"Sample[{pos}] date == date_strs[{idx}]", date_strs[idx], s["date"], exact=True)
    # EMA21 value
    exp_e21 = None if math.isnan(ref_ema21[idx]) else round(float(ref_ema21[idx]), 4)
    check(f"Sample[{pos}] ema21 value correct", exp_e21, s["ema21"], tol=1e-4)
    # EMA55 value
    exp_e55 = None if math.isnan(ref_ema55[idx]) else round(float(ref_ema55[idx]), 4)
    check(f"Sample[{pos}] ema55 value correct", exp_e55, s["ema55"], tol=1e-4)
    # EMA89 value
    exp_e89 = None if math.isnan(ref_ema89[idx]) else round(float(ref_ema89[idx]), 4)
    check(f"Sample[{pos}] ema89 value correct", exp_e89, s["ema89"], tol=1e-4)

# Last sample must be the very last bar
check("Last sample date == last date in input", date_strs[-1], result_ema["ema_samples"][-1]["date"], exact=True)

# Alignment classification
cur_e21 = float(talib_ema21[-1])
cur_e55 = float(talib_ema55[-1])
cur_e89 = float(talib_ema89[-1])
if not any(math.isnan(v) for v in [cur_e21, cur_e55, cur_e89]):
    if cur_e21 > cur_e55 > cur_e89:
        expected_align = "bull"
    elif cur_e21 < cur_e55 < cur_e89:
        expected_align = "bear"
    else:
        expected_align = "mixed"
    check("EMA alignment classification correct", expected_align, result_ema["alignment"], exact=True)
    print(f"    (EMA21={cur_e21:.4f}, EMA55={cur_e55:.4f}, EMA89={cur_e89:.4f} -> '{expected_align}')")

# price_vs_ema21_pct
expected_pct21 = round((ec[-1] - cur_e21) / cur_e21 * 100, 4) if cur_e21 != 0 else None
check("price_vs_ema21_pct correct", expected_pct21, result_ema["price_vs_ema21_pct"], tol=1e-4)

# price_vs_ema55_pct
expected_pct55 = round((ec[-1] - cur_e55) / cur_e55 * 100, 4) if cur_e55 != 0 else None
check("price_vs_ema55_pct correct", expected_pct55, result_ema["price_vs_ema55_pct"], tol=1e-4)

# ─────────────────────────────────────────────────────────────────────────────
# 4. calc_adx — trend_strength classification thresholds
# ─────────────────────────────────────────────────────────────────────────────

section("4. calc_adx — trend_strength classification thresholds")

# From _ta.py source:
#   if cur_adx > 25      -> "strong"
#   elif cur_adx >= 20   -> "trending"
#   else                 -> "ranging"

np.random.seed(42)
n_adx = 100
adx_c = 100.0 + np.cumsum(np.random.randn(n_adx) * 1.0)
adx_h = adx_c + np.abs(np.random.randn(n_adx)) * 0.5
adx_l = adx_c - np.abs(np.random.randn(n_adx)) * 0.5

adx_result = calc_adx(adx_h, adx_l, adx_c, timeframe="1d")
reported_adx = adx_result["adx"]
reported_strength = adx_result["trend_strength"]

# Verify trend_strength classification matches ADX value
if reported_adx is not None:
    if reported_adx > 25:
        expected_strength = "strong"
    elif reported_adx >= 20:
        expected_strength = "trending"
    else:
        expected_strength = "ranging"
else:
    expected_strength = "ranging"

check("trend_strength classification matches ADX value", expected_strength, reported_strength, exact=True)
print(f"    (ADX={reported_adx}, classified as '{reported_strength}')")

# Test exact boundary cases using the classification function mirroring _ta.py
def classify_adx(v):
    """Mirrors _ta.py logic exactly as read from source."""
    if v is None:
        return "ranging"
    if v > 25:
        return "strong"
    elif v >= 20:
        return "trending"
    return "ranging"

boundary_cases = [
    (None,   "ranging"),
    (0.0,    "ranging"),
    (19.99,  "ranging"),
    (20.0,   "trending"),   # boundary: >= 20 -> trending
    (20.01,  "trending"),
    (25.0,   "trending"),   # exactly 25: NOT > 25, so trending (NOT strong)
    (25.01,  "strong"),     # > 25 -> strong
    (50.0,   "strong"),
]

for adx_val, exp_class in boundary_cases:
    got = classify_adx(adx_val)
    check(f"ADX boundary: adx={adx_val} -> '{exp_class}'", exp_class, got, exact=True)

# Verify ADX value itself matches talib
talib_adx_arr = talib.ADX(adx_h.astype("f8"), adx_l.astype("f8"), adx_c.astype("f8"), timeperiod=14)
talib_adx_last = None if math.isnan(talib_adx_arr[-1]) else round(float(talib_adx_arr[-1]), 4)
check("ADX last value matches talib ADX(14)", talib_adx_last, reported_adx, tol=1e-4)

# Verify DI+ and DI-
talib_plus_di  = talib.PLUS_DI(adx_h.astype("f8"), adx_l.astype("f8"), adx_c.astype("f8"), timeperiod=14)
talib_minus_di = talib.MINUS_DI(adx_h.astype("f8"), adx_l.astype("f8"), adx_c.astype("f8"), timeperiod=14)
exp_di_plus  = None if math.isnan(talib_plus_di[-1])  else round(float(talib_plus_di[-1]),  4)
exp_di_minus = None if math.isnan(talib_minus_di[-1]) else round(float(talib_minus_di[-1]), 4)
check("DI+ matches talib PLUS_DI(14)",  exp_di_plus,  adx_result["di_plus"],  tol=1e-4)
check("DI- matches talib MINUS_DI(14)", exp_di_minus, adx_result["di_minus"], tol=1e-4)

# Verify 14d_hi and 14d_lo
valid_adx = talib_adx_arr[~np.isnan(talib_adx_arr)]
if len(valid_adx) >= 14:
    recent14 = valid_adx[-14:]
    exp_14hi = round(float(np.max(recent14)), 4)
    exp_14lo = round(float(np.min(recent14)), 4)
    check("ADX 14d_hi correct", exp_14hi, adx_result["14d_hi"], tol=1e-4)
    check("ADX 14d_lo correct", exp_14lo, adx_result["14d_lo"], tol=1e-4)

# Verify 1w mode structure
adx_1w = calc_adx(adx_h, adx_l, adx_c, timeframe="1w")
check("1w mode returns 'adx' key",              True, "adx"            in adx_1w, exact=True)
check("1w mode returns 'trend_strength' key",   True, "trend_strength" in adx_1w, exact=True)
check("1w mode does NOT return 'di_plus' key",  True, "di_plus"        not in adx_1w, exact=True)
check("1w mode does NOT return 'di_minus' key", True, "di_minus"       not in adx_1w, exact=True)
check("1w mode does NOT return '14d_hi' key",   True, "14d_hi"         not in adx_1w, exact=True)

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

section("SUMMARY")
passed = sum(1 for r in results if r[0] == PASS)
failed = sum(1 for r in results if r[0] == FAIL)
total  = len(results)

print(f"\n  Total : {total}")
print(f"  PASS  : {passed}")
print(f"  FAIL  : {failed}")

if failed > 0:
    print("\n  FAILURES:")
    for status, name, expected, actual in results:
        if status == FAIL:
            print(f"    FAIL  {name}")
            print(f"        expected : {expected!r}")
            print(f"        actual   : {actual!r}")

print()
sys.exit(0 if failed == 0 else 1)
