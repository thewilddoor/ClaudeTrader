# scheduler/tools/registry.py
"""
Registers FMP, Serper, and PyExec as Letta agent tools.
Alpaca is accessed via the Alpaca MCP server — Letta connects to it as an MCP source,
not a registered Python tool.
"""
import os
from letta_client import Letta
from scheduler.tools.fmp import fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar
from scheduler.tools.serper import serper_search
from scheduler.tools.pyexec import run_script


def register_all_tools(agent_id: str, server_url: str | None = None) -> list[str]:
    """
    Register FMP, Serper, and PyExec tools with the Letta agent.
    Returns list of registered tool names.
    """
    url = server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
    client = Letta(base_url=url)
    registered = []

    for fn in [fmp_screener, fmp_ohlcv, fmp_news, fmp_earnings_calendar, serper_search, run_script]:
        try:
            tool = client.tools.upsert_from_function(func=fn)
            client.agents.tools.attach(tool.id, agent_id=agent_id)
            registered.append(tool.name)
        except Exception as e:
            print(f"Warning: could not register tool {fn.__name__}: {e}")

    return registered


def attach_alpaca_mcp(agent_id: str, server_url: str | None = None) -> bool:
    """
    Register the Alpaca MCP server with Letta and attach all its tools to the agent.

    Flow:
      1. Create/register the SSE MCP server with Letta
      2. List tools available from that server (Letta assigns each a tool ID)
      3. Attach every tool to the agent so Claude can call them
    """
    url = server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
    client = Letta(base_url=url)
    alpaca_mcp_url = os.environ.get("ALPACA_MCP_URL", "http://alpaca-mcp:8000/sse")
    try:
        server = client.mcp_servers.create(
            server_name="alpaca",
            config={
                "server_url": alpaca_mcp_url,
                "mcp_server_type": "sse",
            },
        )
        server_id = server.id
        if not server_id:
            print("Warning: Alpaca MCP server created but returned no ID")
            return False

        tools = client.mcp_servers.tools.list(server_id)
        attached = 0
        for tool in tools:
            try:
                client.agents.tools.attach(tool.id, agent_id=agent_id)
                attached += 1
            except Exception as e:
                print(f"Warning: could not attach MCP tool {tool.name}: {e}")

        print(f"Alpaca MCP: registered server '{server_id}', attached {attached}/{len(tools)} tools")
        return attached > 0
    except Exception as e:
        print(f"Warning: could not attach Alpaca MCP: {e}")
        return False
