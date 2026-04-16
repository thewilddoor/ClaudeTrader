"""
Alpaca trading tools implemented as direct HTTP calls to Alpaca's REST API v2.
These bypass the MCP layer so Letta can execute them without private-IP restrictions.

IMPORTANT: Each function must be fully self-contained (imports, helpers inlined)
because Letta's upsert_from_function extracts only the function body and runs it
in an isolated sandbox with no access to module-level code.
"""
from typing import Optional


def alpaca_get_account(
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict:
    """Get Alpaca account information including equity and buying power.

    Args:
        api_key: Alpaca API key; reads from ALPACA_API_KEY env var if not provided.
        secret_key: Alpaca secret key; reads from ALPACA_SECRET_KEY env var if not provided.
        base_url: Alpaca base URL; reads from ALPACA_BASE_URL env var if not provided.

    Returns:
        dict: Account details with equity, buying_power, cash, portfolio_value fields.
    """
    import os
    import requests

    api_key = api_key or os.environ["ALPACA_API_KEY"]
    secret_key = secret_key or os.environ["ALPACA_SECRET_KEY"]
    base = (base_url or os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/")
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}

    r = requests.get(f"{base}/v2/account", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def alpaca_get_positions(
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> list:
    """Get all current open positions in the portfolio.

    Args:
        api_key: Alpaca API key; reads from ALPACA_API_KEY env var if not provided.
        secret_key: Alpaca secret key; reads from ALPACA_SECRET_KEY env var if not provided.
        base_url: Alpaca base URL; reads from ALPACA_BASE_URL env var if not provided.

    Returns:
        list: Open positions with symbol, qty, avg_entry_price, unrealized_pl fields.
    """
    import os
    import requests

    api_key = api_key or os.environ["ALPACA_API_KEY"]
    secret_key = secret_key or os.environ["ALPACA_SECRET_KEY"]
    base = (base_url or os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/")
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}

    r = requests.get(f"{base}/v2/positions", headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def alpaca_place_order(
    symbol: str,
    qty: float,
    side: str,
    order_type: str = "market",
    time_in_force: str = "day",
    limit_price: Optional[float] = None,
    stop_price: Optional[float] = None,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict:
    """Place a buy or sell order for a stock.

    Args:
        symbol: Stock ticker symbol to trade (e.g. AAPL).
        qty: Number of shares to buy or sell (fractional allowed).
        side: Order direction; 'buy' or 'sell'.
        order_type: Order type; 'market', 'limit', 'stop', or 'stop_limit'.
        time_in_force: Order duration; 'day', 'gtc', 'opg', 'cls', 'ioc', or 'fok'.
        limit_price: Limit price in USD; required when order_type is 'limit' or 'stop_limit'.
        stop_price: Stop trigger price in USD; required when order_type is 'stop' or 'stop_limit'.
        api_key: Alpaca API key; reads from ALPACA_API_KEY env var if not provided.
        secret_key: Alpaca secret key; reads from ALPACA_SECRET_KEY env var if not provided.
        base_url: Alpaca base URL; reads from ALPACA_BASE_URL env var if not provided.

    Returns:
        dict: Created order with id, status, symbol, qty, side, type, filled_avg_price fields.
    """
    import os
    import requests

    api_key = api_key or os.environ["ALPACA_API_KEY"]
    secret_key = secret_key or os.environ["ALPACA_SECRET_KEY"]
    base = (base_url or os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/")
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}

    payload = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": order_type,
        "time_in_force": time_in_force,
    }
    if limit_price is not None:
        payload["limit_price"] = str(limit_price)
    if stop_price is not None:
        payload["stop_price"] = str(stop_price)

    r = requests.post(f"{base}/v2/orders", headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def alpaca_list_orders(
    status: str = "open",
    limit: int = 50,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> list:
    """List orders filtered by status.

    Args:
        status: Order status filter; 'open', 'closed', or 'all' (default 'open').
        limit: Maximum number of orders to return (default 50, changeable).
        api_key: Alpaca API key; reads from ALPACA_API_KEY env var if not provided.
        secret_key: Alpaca secret key; reads from ALPACA_SECRET_KEY env var if not provided.
        base_url: Alpaca base URL; reads from ALPACA_BASE_URL env var if not provided.

    Returns:
        list: Order records with id, symbol, qty, side, type, status, filled_avg_price fields.
    """
    import os
    import requests

    api_key = api_key or os.environ["ALPACA_API_KEY"]
    secret_key = secret_key or os.environ["ALPACA_SECRET_KEY"]
    base = (base_url or os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/")
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}

    r = requests.get(
        f"{base}/v2/orders",
        headers=headers,
        params={"status": status, "limit": limit},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def alpaca_cancel_order(
    order_id: str,
    api_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> dict:
    """Cancel an open order by its ID.

    Args:
        order_id: The UUID of the order to cancel (from alpaca_list_orders or alpaca_place_order).
        api_key: Alpaca API key; reads from ALPACA_API_KEY env var if not provided.
        secret_key: Alpaca secret key; reads from ALPACA_SECRET_KEY env var if not provided.
        base_url: Alpaca base URL; reads from ALPACA_BASE_URL env var if not provided.

    Returns:
        dict: Empty dict on success (HTTP 204), or error details if cancellation fails.
    """
    import os
    import requests

    api_key = api_key or os.environ["ALPACA_API_KEY"]
    secret_key = secret_key or os.environ["ALPACA_SECRET_KEY"]
    base = (base_url or os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")).rstrip("/")
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}

    r = requests.delete(f"{base}/v2/orders/{order_id}", headers=headers, timeout=30)
    if r.status_code == 204:
        return {}
    r.raise_for_status()
    return r.json()
