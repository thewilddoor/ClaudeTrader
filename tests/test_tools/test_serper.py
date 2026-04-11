import responses
import pytest
from scheduler.tools.serper import serper_search


@responses.activate
def test_serper_returns_results():
    responses.add(
        responses.POST,
        "https://google.serper.dev/search",
        json={"organic": [{"title": "Apple Q1 earnings beat", "link": "https://example.com", "snippet": "Apple reported..."}]},
        status=200,
    )
    result = serper_search(query="AAPL earnings 2026", api_key="test")
    assert "organic" in result
    assert result["organic"][0]["title"] == "Apple Q1 earnings beat"


@responses.activate
def test_serper_news_returns_results():
    responses.add(
        responses.POST,
        "https://google.serper.dev/news",
        json={"news": [{"title": "Fed raises rates", "link": "https://example.com"}]},
        status=200,
    )
    result = serper_search(query="Fed interest rates", search_type="news", api_key="test")
    assert "news" in result
