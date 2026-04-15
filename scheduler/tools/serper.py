"""
Serper (Google Search API) tool.

IMPORTANT: Each function must be fully self-contained (imports, helpers inlined)
because Letta's upsert_from_function extracts only the function body and runs it
in an isolated sandbox with no access to module-level code.
"""
from typing import Optional


def serper_search(
    query: str,
    search_type: str = "search",
    num: int = 10,
    api_key: Optional[str] = None,
) -> dict:
    """Search the web via Serper (Google Search API).

    Args:
        query: Search query string (e.g. 'AAPL earnings report Q1 2026').
        search_type: Type of search; 'search' for general web results, 'news' for news.
        num: Number of results to return (default 10).
        api_key: Serper API key; reads from SERPER_API_KEY env var if not provided.

    Returns:
        dict: Search results with 'organic' list (search) or 'news' list (news type).
    """
    import os
    import requests

    api_key = api_key or os.environ["SERPER_API_KEY"]
    response = requests.post(
        f"https://google.serper.dev/{search_type}",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
