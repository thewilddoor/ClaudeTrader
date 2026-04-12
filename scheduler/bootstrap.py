# scheduler/bootstrap.py
"""
One-time bootstrap. Creates the Letta agent, registers tools, and seeds memory.
Run manually: python -m scheduler.bootstrap
Never run again after first successful execution.
"""
import os
from pathlib import Path
from scheduler.agent import LettaTraderAgent
from scheduler.tools.registry import register_all_tools, attach_alpaca_mcp
from scheduler.tools.sqlite import bootstrap_db as init_trades_db

AGENT_ID_FILE = Path("/app/state/.agent_id")


def bootstrap():
    print("Initialising SQLite trade store...")
    init_trades_db()
    print("Trade store ready.")

    if AGENT_ID_FILE.exists():
        print("Bootstrap already completed. Agent ID:", AGENT_ID_FILE.read_text().strip())
        return

    agent_name = os.environ.get("LETTA_AGENT_NAME", "claude_trader")
    print(f"Creating Letta agent '{agent_name}'...")
    agent = LettaTraderAgent.create_new(agent_name)
    print(f"Agent created: {agent.agent_id}")

    print("Registering tools...")
    tools = register_all_tools(agent.agent_id)
    print(f"Registered: {tools}")

    print("Attaching Alpaca MCP server...")
    ok = attach_alpaca_mcp(agent.agent_id)
    print(f"Alpaca MCP attached: {ok}")

    # Load script library index into agent memory
    index_path = Path("/app/scripts/indicators/index.json")
    if index_path.exists():
        index_content = index_path.read_text()
        agent.send_session(
            f"BOOTSTRAP: Load this indicator library index into your memory for future reference.\n\n{index_content}"
        )
        print("Indicator library loaded.")

    # Save agent ID for scheduler use
    AGENT_ID_FILE.write_text(agent.agent_id)
    print(f"Bootstrap complete. Agent ID saved to {AGENT_ID_FILE}")


if __name__ == "__main__":
    bootstrap()
