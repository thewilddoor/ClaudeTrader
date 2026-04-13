"""
One-time live agent update: replace strategy_doc with the new boundary-first v2 design.

Changes from old doc:
- Adds Role + Objective sections (goal: grow the account)
- Replaces prescriptive TA entry criteria with open boundary model
- Lists all 11 tools explicitly (Claude now knows fmp_news, serper, earnings calendar)
- Describes run_script as a full Python runtime, not just an indicator launcher
- Documents trade_open full signature and r_multiple calculation requirement
- Scopes proposed_change to eod_reflection / weekly_review only
- Removes stale Market Regime section

Usage (inside Docker):
    docker compose exec -e PYTHONPATH=/app scheduler python scripts/one_time/update_strategy_doc_v2.py
"""
import re
import sys
from pathlib import Path
import os

AGENT_ID_FILE = Path(os.environ.get("AGENT_ID_FILE", "/app/state/.agent_id"))

if not AGENT_ID_FILE.exists():
    local = Path(__file__).parent.parent.parent / "state" / ".agent_id"
    if local.exists():
        AGENT_ID_FILE = local
    else:
        print(f"ERROR: agent ID file not found at {AGENT_ID_FILE}")
        sys.exit(1)

from scheduler.agent import LettaTraderAgent, INITIAL_STRATEGY_DOC

agent_id = AGENT_ID_FILE.read_text().strip()
agent = LettaTraderAgent(agent_id=agent_id)

current_doc = agent.get_memory_block("strategy_doc") or ""
print(f"Current strategy_doc: {len(current_doc)} chars")

# Preserve the version metadata block prepended by the strategy gate
metadata_match = re.match(r"(## Version metadata\n(?:[^\n]+\n)*\n)", current_doc)
metadata_block = metadata_match.group(1) if metadata_match else ""

new_doc = metadata_block + INITIAL_STRATEGY_DOC
print(f"New strategy_doc: {len(new_doc)} chars")

agent.update_memory_block("strategy_doc", new_doc)
print("strategy_doc updated.")

print("\nSending live update session to agent...")
response = agent.send_session("""SYSTEM UPDATE: Your strategy document has been redesigned. Read the updated strategy_doc in your core memory now.

Key changes:
1. You now have an explicit Role and Objective — you are an autonomous portfolio manager whose goal is to grow the $50k account through positive-expectancy trading.

2. The old prescriptive entry criteria (EMA, RSI, volume thresholds) have been removed. They were defaults, not rules. You now have full discretion over what approach to use — TA, fundamentals, news, earnings, macro regime, or any combination. The only constraint is having a thesis backed by evidence.

3. All 11 tools are now listed explicitly in your strategy_doc with their purpose. Pay particular attention to: fmp_news, fmp_earnings_calendar, and serper_search — these are available for stock selection and thesis building, not just for health_check defensive monitoring.

4. run_script is a full Python runtime. You can write custom analysis code, not just run the pre-built indicator scripts.

5. trade_open requires these fields: ticker, side, entry_price, size, setup_type, hypothesis_id, rationale, vix_at_entry, regime. Always pass all of them.

6. proposed_change in session JSON is only processed during eod_reflection and weekly_review sessions. Emitting it during pre_market or market_open will be silently ignored.

Acknowledge you have read the updated strategy_doc and understand these changes.""")

print(f"\nAgent response:\n{response}")
print("\nUpdate complete.")
