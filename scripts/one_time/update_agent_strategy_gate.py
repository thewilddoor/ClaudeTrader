"""
One-time live agent update: strategy change gate + context_json on trade_open.
Run once after merging feature/strategy-change-gate to main, with Docker stack up.

Usage (inside Docker):
    docker-compose exec scheduler python scripts/one_time/update_agent_strategy_gate.py

Usage (local venv, Letta reachable at localhost:8283):
    source .venv/bin/activate
    python scripts/one_time/update_agent_strategy_gate.py
"""
import os
import sys
from pathlib import Path

AGENT_ID_FILE = Path(os.environ.get("AGENT_ID_FILE", "/app/state/.agent_id"))

if not AGENT_ID_FILE.exists():
    # Try local dev path as fallback
    local = Path(__file__).parent.parent.parent / "state" / ".agent_id"
    if local.exists():
        AGENT_ID_FILE = local
    else:
        print(f"ERROR: agent ID file not found at {AGENT_ID_FILE}")
        sys.exit(1)

from scheduler.agent import LettaTraderAgent

agent_id = AGENT_ID_FILE.read_text().strip()
agent = LettaTraderAgent(agent_id=agent_id)

# --- Patch strategy_doc if protocol section missing ---
current_doc = agent.get_memory_block("strategy_doc") or ""
print(f"Current strategy_doc: {len(current_doc)} chars")

if "proposed_change" in current_doc and "Strategy change protocol" in current_doc:
    print("Strategy change protocol already present — no doc update needed.")
else:
    print("Protocol section missing — patching strategy_doc...")

    protocol_section = """
## Strategy change protocol
Never write changes to this document directly. Emit proposed changes as
proposed_change in your session JSON output. The system will pre-screen
filterable changes against historical trade data and apply all changes
with version tracking. You will see the result — confirmed or reverted
with performance numbers — in your next session.

Proposed change format (in your session JSON):
  "proposed_change": {
    "description": "human-readable summary",
    "new_strategy_doc": "full updated strategy doc text (no version metadata block)",
    "filter_sql": "optional SQL condition — only for entry filters on trades table columns"
  }

filter_sql examples (uses context_json for indicator values):
  json_extract(context_json, '$.rsi') < 65 AND setup_type = 'momentum'
  regime != 'bear_high_vol'
  vix_at_entry < 25 AND setup_type = 'momentum'
Only include filter_sql if the change is a quantitative entry filter you can express as SQL."""

    if "## Market Regime" in current_doc:
        new_doc = current_doc.replace("## Market Regime", protocol_section + "\n\n## Market Regime")
    else:
        new_doc = current_doc + "\n" + protocol_section

    agent.update_memory_block("strategy_doc", new_doc)
    print(f"strategy_doc updated: {len(new_doc)} chars")

# --- Send live session update ---
print("\nSending live update session to agent...")
response = agent.send_session("""SYSTEM UPDATE: Two new capabilities are now live. Read carefully.

1. STRATEGY CHANGE GATE
Your strategy_doc is now version-tracked. You MUST NOT write changes to strategy_doc directly during sessions. Instead, include a proposed_change object in your session JSON output. The scheduler will:
- Pre-screen entry-filter changes against 60 days of real trade history
- Block changes that would have removed net-profitable trades
- Apply all non-blocked changes as a probationary version
- Auto-promote after 10 trades (if filter_sql was tested) or 20 trades (qualitative)
- Auto-revert if win rate drops >15pp or avg-R drops >0.5 from baseline

Format for proposed changes in your session JSON:
{
  "proposed_change": {
    "description": "concise description of what changes and why",
    "new_strategy_doc": "full strategy doc body text — no version metadata block",
    "filter_sql": "json_extract(context_json, '$.rsi') < 65 AND setup_type = 'momentum'"
  }
}

filter_sql is OPTIONAL. Include it only when the change is a quantitative entry filter expressible as SQL against trades table columns (regime, setup_type, vix_at_entry) or context_json indicator values.

You will receive feedback in your next session prompt if a change was blocked or reverted.

2. context_json PARAMETER ON trade_open
trade_open now accepts an optional context_json parameter — a JSON string of indicator values at entry time:

  trade_open(ticker="NVDA", side="buy", entry_price=850.0, size=10,
             setup_type="momentum", hypothesis_id="H001", rationale="...",
             vix_at_entry=15.2, regime="bull", stop_loss=825.0, take_profit=900.0,
             context_json='{"rsi": 63.2, "adx": 28.1, "volume_ratio": 1.4, "atr": 3.2}')

Pass it whenever you have indicator data. Without it, filter_sql proposals can only reference regime, setup_type, and vix_at_entry. With it, any recorded indicator becomes available for pre-screen evaluation.

Acknowledge you understand both features.""")

print(f"\nAgent response:\n{response}")
print("\nUpdate complete.")
