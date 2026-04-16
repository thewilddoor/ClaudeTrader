"""
One-time script: patch live agent's strategy_doc to add System Constraints section
and update the Hard Limits run_script line.

Run on VPS:
  docker-compose exec scheduler python scripts/one_time/patch_strategy_constraints.py
"""
import os
import sys

sys.path.insert(0, "/app")

from scheduler.agent import LettaTraderAgent

SYSTEM_CONSTRAINTS = """\n## System Constraints\nHard limits (not overridable): API 30s timeout; run_script 60s/512MB; backtest 60 days; one probation max.\n"""

OLD_HARD_LIMIT_LINE = "- run_script: sandboxed — no credentials injected, 256MB RAM, 30s timeout."
NEW_HARD_LIMIT_LINE = "- run_script: sandboxed — no credentials injected, 512MB RAM, 60s timeout."


def main():
    state_path = os.environ.get("AGENT_STATE_PATH", "/app/state/.agent_id")
    with open(state_path) as f:
        agent_id = f.read().strip()

    agent = LettaTraderAgent(agent_id=agent_id)
    current = agent.get_memory_block("strategy_doc")

    if current is None:
        print("ERROR: strategy_doc block not found")
        sys.exit(1)

    updated = current

    # Patch hard limits line
    if OLD_HARD_LIMIT_LINE in updated:
        updated = updated.replace(OLD_HARD_LIMIT_LINE, NEW_HARD_LIMIT_LINE)
        print("Patched: run_script hard limits line updated to 512MB/60s")
    else:
        print("INFO: Hard limits line already updated or not found — skipping")

    # Append system constraints if not already present
    if "## System Constraints" in updated:
        print("INFO: ## System Constraints already present — skipping append")
    else:
        updated = updated.rstrip() + SYSTEM_CONSTRAINTS
        print("Appended: ## System Constraints section added")

    if updated == current:
        print("No changes needed.")
        return

    agent.update_memory_block("strategy_doc", updated)
    print("Done: strategy_doc patched successfully.")


if __name__ == "__main__":
    main()
