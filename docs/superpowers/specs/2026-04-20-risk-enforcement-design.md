# Risk Enforcement Design Spec

**Date:** 2026-04-20
**Scope:** Three confirmed production gaps — broker-side stops, fill price writeback + server-side P&L, daily halt code enforcement

---

## Problem

Three independent flaws cause the system's performance data and risk limits to be unreliable:

1. **No broker-side stops.** `stop_loss` is stored in SQLite but never sent to Alpaca as an order. Positions are unprotected for up to 3.5-hour gaps between sessions. The 1% risk-per-trade guarantee is not mechanically enforced. Paper trading performance data is systematically optimistic because intraday stop-outs never occur.

2. **Entry price never updated with actual fill.** `trade_open` records the pre-order price from `fmp_check_current_price`. The actual `filled_avg_price` from Alpaca is available after the order fills but is never written back. Every R-multiple and P&L figure in the strategy gate's backtest uses phantom prices. Claude also computes `outcome_pnl` and `r_multiple` manually — introducing arithmetic error into the feedback loop.

3. **Daily halt is prompt-only.** The -3% daily loss circuit breaker exists only as a text instruction in OPERATIONS_MANUAL. No code enforces it. Claude could miscalculate, not check, or continue trading through a bad day. The strategy gate accumulates data showing the strategy "survived" days where it should have halted.

---

## Solution

### 1. Broker-Side Stop Orders (F1)

**Schema change:** Add two columns to the `trades` table:
- `alpaca_order_id TEXT` — the entry order ID from Alpaca
- `stop_order_id TEXT` — the GTC stop-loss order ID placed after fill

**New tool:** `trade_update_fill(trade_id, filled_avg_price, alpaca_order_id)` — updates `entry_price` and `alpaca_order_id` on an open trade record. Called once after fill confirmation.

**Market_open protocol (updated):**
```
c. trade_open(...)  → get trade_id
d. alpaca_place_order(symbol, qty, side, order_type="market")
e. alpaca_list_orders(status="closed") → confirm fill, get filled_avg_price + order_id
f. trade_update_fill(trade_id, filled_avg_price, alpaca_order_id)
g. alpaca_place_order(symbol, qty, opposite_side, order_type="stop",
                      stop_price=stop_loss, time_in_force="gtc")
   → store stop_order_id in today_context alongside trade_id
h. hypothesis_log(id, "testing", f"Opened trade_id {trade_id} at {filled_avg_price}")
```

**Manual close protocol (updated):** Before any position close at health_check or EOD, Claude must:
1. Retrieve `stop_order_id` from today_context
2. `alpaca_cancel_order(stop_order_id)` — cancel the standing stop
3. `alpaca_place_order(...)` — place the market close
4. `trade_close(...)` — record exit

If the stop order has already filled (stop was hit intraday), `alpaca_cancel_order` will return an error — Claude treats this as "stop already executed, record the close using the stop price."

**Shorts:** For short positions the stop is a buy-stop above entry. Same protocol applies.

---

### 2. Fill Price Writeback + Server-Side P&L (F2)

**`trade_update_fill(trade_id, filled_avg_price, alpaca_order_id)`**
- Updates `entry_price = filled_avg_price` and `alpaca_order_id` on the trades row
- Validates the trade is still open (no `closed_at`)
- Returns `{"status": "ok", "trade_id": trade_id, "entry_price": filled_avg_price}`

**`trade_close` simplified:**
- Remove `outcome_pnl` and `r_multiple` as required inputs from Claude
- Compute server-side from stored fields:
  ```python
  outcome_pnl = (exit_price - entry_price) * size  # long
  outcome_pnl = (entry_price - exit_price) * size  # short
  risk = abs(entry_price - stop_loss) * size
  r_multiple = outcome_pnl / risk if risk > 0 else 0.0
  ```
- Claude passes only: `trade_id`, `exit_price`, `exit_reason`
- `outcome_pnl` and `r_multiple` become optional overrides (accepted if provided, ignored in favour of computed values)

**Tool schema update:** `trade_update_fill` added to TOOL_SCHEMAS and `_build_tool_functions`. `trade_close` schema updated to mark `outcome_pnl` and `r_multiple` as optional.

---

### 3. Daily Halt Code Enforcement (F3)

**New function `_check_daily_halt() -> bool`** in `main.py`:
- Queries today's closed trades: `SELECT SUM(outcome_pnl) FROM trades WHERE date(closed_at) = date('now') AND closed_at IS NOT NULL`
- Fetches current equity from `alpaca_get_account()`
- Returns `True` (halted) if `sum_pnl / equity < -0.03`
- Returns `False` if no data or threshold not breached

**Integrated into `job_market_open` and `job_health_check`:**
```python
def job_market_open():
    if _check_daily_halt():
        log.warning("Daily halt active — skipping market_open session")
        send_telegram("🛑 DAILY HALT: -3% loss threshold reached. market_open skipped.")
        return
    # ... existing logic
```

**Not applied to:** `health_check` position *closing* — the halt only blocks new position opens. If Claude is closing positions at health_check, that should proceed regardless.

Refinement: `job_health_check` checks halt only before the session runs. Within the session, the OPERATIONS_MANUAL already instructs Claude not to seek new setups if the halt condition is met — the code check is a belt-and-suspenders enforcement.

---

## Files Changed

| File | Change |
|---|---|
| `scheduler/tools/sqlite.py` | Add `trade_update_fill()`; modify `trade_close()` to compute P&L server-side; add schema migration for `alpaca_order_id` + `stop_order_id` columns |
| `scheduler/agent.py` | Add `trade_update_fill` to `TOOL_SCHEMAS` and `_build_tool_functions`; update `OPERATIONS_MANUAL` market_open protocol + close protocol; update `trade_close` schema (optional overrides) |
| `scheduler/main.py` | Add `_check_daily_halt()`; call it in `job_market_open` and `job_health_check` |
| `tests/test_sqlite.py` | Tests for `trade_update_fill`, server-side P&L computation, new schema columns, `trade_close` with and without override values |
| `tests/test_main.py` | Tests for `_check_daily_halt()` — halt fires at -3%, skips below threshold, handles no-data case |
| `tests/test_agent.py` | Test that `trade_update_fill` is in TOOL_SCHEMAS; test `trade_close` schema marks outcome_pnl/r_multiple optional |

---

## Success Criteria

1. Every open trade has a corresponding live stop order at Alpaca within the same market_open session
2. `trades.entry_price` reflects actual `filled_avg_price` for all trades opened after this change
3. `trades.outcome_pnl` and `trades.r_multiple` are computed from stored DB fields, not from Claude's arithmetic
4. `job_market_open` and `job_health_check` do not call `run_session` when today's realized P&L is below -3% of equity
5. Claude's close protocol cancels the standing stop order before placing a manual close

---

## Out of Scope

- Bracket orders / take-profit enforcement at Alpaca (Claude manages upside exits)
- Market holiday calendar (low impact, separate concern)
- Digest truncation improvements (supplementary context, not correctness issue)
- Stop order trailing / adjustment (future enhancement)
