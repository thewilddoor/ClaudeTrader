# scheduler/tools/registry.py
"""
Registers all tools with the Letta agent.
FMP, Serper, PyExec, and Alpaca are registered as direct Python tools (not MCP)
so they work reliably inside the Docker network without private-IP restrictions.
"""
import os
from letta_client import Letta
from scheduler.tools.fmp import fmp_screener, fmp_ta, fmp_check_current_price, fmp_news, fmp_earnings_calendar
from scheduler.tools.serper import serper_search
from scheduler.tools.pyexec import run_script
from scheduler.tools.alpaca import (
    alpaca_get_account,
    alpaca_get_positions,
    alpaca_place_order,
    alpaca_list_orders,
    alpaca_cancel_order,
)
from scheduler.tools.sqlite import (
    trade_open,
    trade_close,
    hypothesis_log,
    trade_query,
)

ALL_TOOLS = [
    fmp_screener,
    fmp_ta,
    fmp_check_current_price,
    fmp_news,
    fmp_earnings_calendar,
    serper_search,
    run_script,
    alpaca_get_account,
    alpaca_get_positions,
    alpaca_place_order,
    alpaca_list_orders,
    alpaca_cancel_order,
    trade_open,
    trade_close,
    hypothesis_log,
    trade_query,
]


def register_all_tools(agent_id: str, server_url: str | None = None) -> list[str]:
    """Register all trading tools with the Letta agent.

    Args:
        agent_id: Letta agent ID to attach tools to.
        server_url: Letta server base URL (reads from LETTA_SERVER_URL env var if not provided).

    Returns:
        list: Names of successfully registered tools.
    """
    url = server_url or os.environ.get("LETTA_SERVER_URL", "http://localhost:8283")
    client = Letta(base_url=url)
    registered = []

    for fn in ALL_TOOLS:
        try:
            tool = client.tools.upsert_from_function(func=fn)
            client.agents.tools.attach(tool.id, agent_id=agent_id)
            registered.append(tool.name)
        except Exception as e:
            print(f"Warning: could not register tool {fn.__name__}: {e}")

    return registered


def attach_alpaca_mcp(agent_id: str, server_url: str | None = None) -> bool:
    """Attempt to register the Alpaca MCP server with Letta (best-effort).

    This is a fallback path; Alpaca tools are already registered as direct Python
    tools in register_all_tools. This MCP path fails silently in Docker environments
    where Letta's private-IP security check blocks the Docker network address.

    Args:
        agent_id: Letta agent ID (unused here, kept for API compatibility).
        server_url: Letta server base URL (reads from LETTA_SERVER_URL env var if not provided).

    Returns:
        bool: True if MCP server was registered (tools may not be accessible via MCP).
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
        print(f"Alpaca MCP server registered: {server.id}")
        return True
    except Exception as e:
        # 409 = already exists from a previous bootstrap run — that's fine
        if "409" in str(e) or "unique" in str(e).lower():
            print("Alpaca MCP server already registered (skipping).")
            return True
        print(f"Warning: could not register Alpaca MCP server: {e}")
        return False
