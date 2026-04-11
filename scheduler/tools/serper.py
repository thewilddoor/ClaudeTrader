import os
import requests
from typing import Optional

SERPER_BASE = "https://google.serper.dev"


def serper_search(
    query: str,
    search_type: str = "search",
    num: int = 10,
    api_key: Optional[str] = None,
) -> dict:
    """
    Search the web via Serper (Google Search API).
    search_type: 'search' for general, 'news' for news results.
    Returns dict with 'organic' (search) or 'news' (news) list.
    """
    api_key = api_key or os.environ["SERPER_API_KEY"]
    response = requests.post(
        f"{SERPER_BASE}/{search_type}",
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        json={"q": query, "num": num},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()
