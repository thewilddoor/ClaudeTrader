"""
One-time script: replace the live agent's strategy_doc with the expanded v1 operations manual.

This script:
1. Reads the current strategy_doc from the live agent.
2. Extracts the version metadata block (the scheduler-owned header) if present.
3. Replaces the body with the new INITIAL_STRATEGY_DOC content.
4. Writes back to Letta — the block limit is updated to 35,000 chars via the Letta blocks API.

Run on VPS after deploying the code change:
  docker compose exec -e PYTHONPATH=/app scheduler python scripts/one_time/expand_strategy_doc.py

The script is idempotent — if the new content is already present it prints a message and exits.
"""
import os
import re
import sys

sys.path.insert(0, "/app")

from scheduler.agent import LettaTraderAgent, INITIAL_STRATEGY_DOC
from letta_client import Letta


STRATEGY_DOC_BLOCK_LIMIT = 35000
# Signature phrase present in the new doc but not the old one
NEW_DOC_SIGNATURE = "Role and Mandate"


def main():
    state_path = os.environ.get("AGENT_STATE_PATH", "/app/state/.agent_id")
    with open(state_path) as f:
        agent_id = f.read().strip()

    agent = LettaTraderAgent(agent_id=agent_id)

    current = agent.get_memory_block("strategy_doc")
    if current is None:
        print("ERROR: strategy_doc block not found on agent", agent_id)
        sys.exit(1)

    # Check if already updated
    if NEW_DOC_SIGNATURE in current:
        print("INFO: strategy_doc already contains the expanded content. Nothing to do.")
        return

    # Preserve existing version metadata block (lines starting with ## Version metadata
    # through the first blank line after it), if present.
    metadata_match = re.match(
        r'^(## Version metadata\n(?:[^\n]+\n)*\n)',
        current,
        flags=re.MULTILINE,
    )
    metadata_prefix = metadata_match.group(1) if metadata_match else ""

    new_doc = metadata_prefix + INITIAL_STRATEGY_DOC

    if len(new_doc) > STRATEGY_DOC_BLOCK_LIMIT:
        print(f"ERROR: new_doc length {len(new_doc)} exceeds block limit {STRATEGY_DOC_BLOCK_LIMIT}")
        sys.exit(1)

    # Update block limit via Letta REST API directly, then update value
    server_url = os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
    raw_client = Letta(base_url=server_url)

    blocks = list(raw_client.agents.blocks.list(agent_id=agent_id))
    strategy_block = next((b for b in blocks if b.label == "strategy_doc"), None)
    if strategy_block is None:
        print("ERROR: strategy_doc block not found in agent blocks list")
        sys.exit(1)

    print(f"Current strategy_doc block: id={strategy_block.id}, limit={strategy_block.limit}, "
          f"value_len={len(strategy_block.value)}")
    print(f"New content: {len(new_doc)} chars, limit will be set to {STRATEGY_DOC_BLOCK_LIMIT}")

    raw_client.blocks.update(
        block_id=strategy_block.id,
        value=new_doc,
        limit=STRATEGY_DOC_BLOCK_LIMIT,
    )

    print("Done: strategy_doc expanded to full operations manual with 35,000 char limit.")
    print(f"New content length: {len(new_doc)} chars.")


if __name__ == "__main__":
    main()
