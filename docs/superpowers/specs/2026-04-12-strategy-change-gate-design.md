# Strategy Change Evaluation Gate — Design

**Date:** 2026-04-12  
**Status:** Approved  
**Scope:** Intercept Claude's proposed strategy changes, pre-screen backtestable ones against historical trade data, apply all changes as probationary versions, auto-promote or auto-revert after a minimum number of real trades.

---

## Problem

Claude writes strategy changes directly to `strategy_doc` during EOD and weekly sessions. There is no validation step — a change that would have removed net-profitable trades, or that degrades real performance after deployment, goes live immediately and stays live until Claude decides to revise it again. The self-learning loop depends on Claude's narrative reasoning to catch bad changes, which is unreliable.

---

## Design Principle

"Is this change good?" cannot be answered reliably before deployment for qualitative changes. For backtestable changes (entry filters with SQL-expressible conditions), historical replay gives a rough pre-screen. For both types, the authoritative answer is real post-deployment performance. The gate therefore uses **probationary versioning**: every change goes live immediately but is tagged probationary, and the scheduler auto-promotes or auto-reverts after a minimum number of closed trades under the new version.

---

## Flow Overview

```
EOD/weekly session output
        │
        ▼
proposed_change in JSON?
        │
     ┌──┴──────────────────┐
    yes                    no
        │                    └─► session ends normally
        ▼
probationary version active?
        │
     ┌──┴──────────────────┐
    yes                    no
        │                    │
        ▼                    ▼
  write feedback         filter_sql present?
  to pending_feedback       │
  .txt (append)          ┌──┴──────────────────┐
  return                yes                    no
                            │               (qualitative)
                            ▼                   │
                       run pre-screen           │
                            │                   │
                  ┌─────────┴──────────┐        │
                blocked            passed        │
                  │                   │          │
                  ▼                   ▼          ▼
             write feedback    promote_after=10  promote_after=20
             to pending_feedback.txt             │
             return                              │
                                                 ▼
                                        apply_change:
                                          snapshot baseline
                                          insert strategy_versions row
                                          write strategy_doc to Letta
                                          send Telegram: probation started

[After every EOD session]
        │
        ▼
check_probation:
  count closed trades WHERE strategy_version = current probationary
  < promote_after → nothing
  ≥ promote_after → evaluate
        │
     ┌──┴──────────────────────────┐
  passed                       degraded
  (win_rate drop ≤ 15pp          (win_rate drop > 15pp
   AND avg_r drop ≤ 0.5)          OR avg_r drop > 0.5)
        │                              │
        ▼                              ▼
  update row: confirmed          update row: reverted
  update strategy_doc            restore last confirmed doc_text
  metadata block in Letta        write to Letta
  Telegram: promoted             append revert feedback to
                                 pending_feedback.txt
                                 Telegram: reverted with numbers
```

---

## Data Model Changes

### `trades` table — two new columns

```sql
ALTER TABLE trades ADD COLUMN strategy_version TEXT;
ALTER TABLE trades ADD COLUMN context_json TEXT;
```

`strategy_version` is stamped automatically by `trade_open` — Claude does not pass it. The tool queries `strategy_versions` to get the current active version:

```sql
SELECT version FROM strategy_versions 
WHERE status IN ('confirmed', 'probationary')
ORDER BY created_at DESC LIMIT 1
```

`context_json` is an optional JSON blob Claude passes to `trade_open` containing indicator values at entry time (e.g., `{"rsi": 63.2, "adx": 28.1, "volume_ratio": 1.4, "atr": 3.2}`). This is what makes `filter_sql` pre-screens expressive — without it, filters can only reference `regime`, `setup_type`, and `vix_at_entry`.

### New `strategy_versions` table

```sql
CREATE TABLE IF NOT EXISTS strategy_versions (
    version           TEXT    PRIMARY KEY,
    status            TEXT    NOT NULL CHECK(status IN ('confirmed','probationary','reverted')),
    doc_text          TEXT    NOT NULL,
    baseline_win_rate REAL,
    baseline_avg_r    REAL,
    promote_after     INTEGER NOT NULL,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at       TEXT,
    revert_reason     TEXT
);
```

`doc_text` is the full strategy doc at the moment of version creation — this is what revert restores to core memory. `baseline_*` metrics are snapshotted from the trades table at change time. `promote_after` is 10 for backtestable-and-passed, 20 for qualitative. `trades_counted` is intentionally absent — derived from the trades table to avoid drift.

### Strategy doc — version metadata block

The scheduler writes a metadata block at the top of every strategy doc it applies. Claude is instructed not to modify these fields:

```
## Version metadata
version: v6
status: probationary
promote_after: 10
baseline_win_rate: 55.0
baseline_avg_r: 1.40
```

### Seed row

`bootstrap_db()` creates the schema only. After `LettaTraderAgent.create_new()` writes `INITIAL_STRATEGY_DOC` to core memory, `bootstrap.py` inserts the v1 confirmed row into `strategy_versions` using the same `INITIAL_STRATEGY_DOC` constant. This keeps the doc text in one place and ensures the seed row and core memory are always identical.

---

## Claude's Proposed Change JSON

Claude emits a `proposed_change` object in its EOD or weekly session JSON output. Claude does **not** write to `strategy_doc` directly — the strategy doc includes a protocol instruction enforcing this.

```json
{
  "proposed_change": {
    "description": "Only take momentum entries when RSI at entry < 65",
    "new_strategy_doc": "# Strategy Document v6\n...",
    "filter_sql": "json_extract(context_json, '$.rsi') < 65 AND setup_type = 'momentum'"
  }
}
```

`filter_sql` is optional. If absent, the change is treated as qualitative (20-trade probation, no pre-screen). `new_strategy_doc` is the full proposed strategy doc text without the version metadata block — the scheduler prepends that block before writing to Letta.

---

## Pre-Screen Logic (backtestable path only)

Runs only when `filter_sql` is present. Both queries exclude `context_json IS NULL` rows to avoid NULL-comparison bias from trades recorded before the `context_json` column existed.

**Would-be-removed trades:**

```sql
SELECT AVG(r_multiple) AS avg_r_blocked, COUNT(*) AS n
FROM trades
WHERE NOT ({filter_sql})
  AND context_json IS NOT NULL
  AND closed_at > datetime('now', '-60 days')
  AND closed_at IS NOT NULL
```

**Block condition:** `avg_r_blocked > 0` — the trades the rule would have removed were net profitable. The threshold is zero, not the overall average: a filter that removes mediocre-but-positive trades should still be blocked. If blocked, feedback is written to `pending_feedback.txt` and the change does not enter probation.

---

## Pending Feedback File

Path: `/app/state/pending_feedback.txt`

All writes use **append mode**. Multiple feedback messages can accumulate in a single EOD (e.g., one-at-a-time guard fires, then probation check reverts in the same pass). The read-and-clear step at the start of the next EOD or weekly session prompt build slurps the entire file and deletes it. The full accumulated content is appended to the session prompt as `| FEEDBACK: <text>`.

---

## Fallback: Claude Writes strategy_doc Directly

At the start of every EOD and weekly session, `run_session` reads the current `strategy_doc` value and stores it. After the session completes, it reads again. If the version field changed and there is no `proposed_change` in Claude's JSON output, Claude bypassed the gate. The scheduler:

1. Treats the new doc text as a qualitative proposed change (20-trade probation)
2. Snapshots baseline metrics and inserts a `strategy_versions` row
3. Sends Telegram: "Claude wrote strategy_doc directly — change captured and wrapped in probation"

---

## New File: `scheduler/strategy_gate.py`

Four functions, no Letta tool registration — this is scheduler-side logic only.

| Function | Purpose |
|---|---|
| `run_prescreen(filter_sql, db_path)` | Returns `{blocked, avg_r_blocked, trades_evaluated}` |
| `snapshot_baseline_metrics(db_path)` | Returns `{win_rate, avg_r}` from current active version's trades |
| `apply_change(agent, proposed_change, db_path)` | Raises if probationary version active; otherwise inserts row, writes to Letta |
| `check_probation(agent, db_path)` | Derives trade count, promotes or reverts; appends to pending_feedback.txt on revert |

---

## Modified Files Summary

| File | Change |
|---|---|
| `scheduler/tools/sqlite.py` | Schema: two new columns, new table, `ALTER TABLE` in `bootstrap_db`; `trade_open` gains `context_json` param and version-stamping |
| `scheduler/bootstrap.py` | After agent creation, insert v1 row into `strategy_versions` using `INITIAL_STRATEGY_DOC` |
| `scheduler/agent.py` | Add `update_memory_block(block_name, value)` method |
| `scheduler/main.py` | `run_session` intercepts `proposed_change`; calls `check_probation` after EOD/weekly; reads+clears pending_feedback.txt before prompt build |
| `scheduler/sessions.py` | `build_eod_reflection_prompt` and `build_weekly_review_prompt` accept `pending_feedback: Optional[str]` |
| `scheduler/notifier.py` | Five new formatters: probation start, promotion, revert, gate block, bypass alert |
| `scheduler/strategy_gate.py` | New file (see above) |

---

## Strategy Doc Protocol Instruction

Added to `INITIAL_STRATEGY_DOC` in `agent.py` and therefore present in every strategy doc from v1 onward:

```
## Strategy change protocol
Never write changes to this document directly. Emit proposed changes as 
proposed_change in your session JSON output. The system will pre-screen 
filterable changes against historical trade data and apply all changes 
with version tracking. You will see the result — confirmed or reverted 
with performance numbers — in your next session.
```

---

## Error Handling

| Condition | Response |
|---|---|
| `filter_sql` contains blocked SQL keyword (INSERT/UPDATE/etc.) | Treat as malformed, reject with feedback; do not enter probation |
| `strategy_versions` table empty at `trade_open` time | Version stamped as NULL; trade records normally |
| Letta API write fails during `apply_change` | `strategy_versions` row is not inserted (write Letta first, then insert row only on success); Telegram alert |
| Letta API write fails during revert | Log error + Telegram alert; do not update `strategy_versions` row status (will retry next EOD) |
| Pre-screen SQL error (malformed `filter_sql`) | Treat as non-backtestable (qualitative path, 20-trade probation); log warning |

---

## What Is Not Changing

- The five-session schedule is unchanged
- The eleven existing Letta tools are unchanged (trade_open gains a new optional parameter, fully backwards-compatible)
- Letta recall memory usage is unchanged
- The existing `trade_query` keyword block list is unchanged
- Docker Compose services and volumes are unchanged
