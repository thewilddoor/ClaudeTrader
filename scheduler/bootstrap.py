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
        agent_id = AGENT_ID_FILE.read_text().strip()
        print(f"Agent already exists ({agent_id}). Re-registering tools...")
        tools = register_all_tools(agent_id)
        print(f"Registered: {tools}")
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

    # Seed v1 row in strategy_versions — metadata block prepended for consistency with all later versions
    from scheduler.tools.sqlite import _connect as _db_connect
    from scheduler.agent import INITIAL_STRATEGY_DOC
    _v1_metadata = (
        "## Version metadata\n"
        "version: v1\n"
        "status: confirmed\n"
        "promote_after: 20\n"
        "baseline_win_rate: null\n"
        "baseline_avg_r: null\n\n"
    )
    _v1_doc_text = _v1_metadata + INITIAL_STRATEGY_DOC
    db_conn = _db_connect()
    try:
        if db_conn.execute("SELECT version FROM strategy_versions WHERE version='v1'").fetchone() is None:
            db_conn.execute(
                "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
                "VALUES ('v1', 'confirmed', ?, 20)",
                (_v1_doc_text,),
            )
            db_conn.commit()
            print("Seeded strategy_versions with v1 (confirmed).")
    finally:
        db_conn.close()

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
