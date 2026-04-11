# tests/conftest.py
import os
import pytest

@pytest.fixture(autouse=True)
def set_test_env(monkeypatch):
    """Set dummy env vars so tools don't require real keys in unit tests."""
    monkeypatch.setenv("FMP_API_KEY", "test_fmp_key")
    monkeypatch.setenv("SERPER_API_KEY", "test_serper_key")
    monkeypatch.setenv("ALPACA_API_KEY", "test_alpaca_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_alpaca_secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_tg_token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "test_tg_chat")
    monkeypatch.setenv("LETTA_SERVER_URL", "http://localhost:8283")
    monkeypatch.setenv("SCRIPTS_DIR", "scripts")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test_anthropic_key")
    monkeypatch.setenv("LETTA_AGENT_NAME", "test_trader")
    monkeypatch.setenv("ALPACA_MCP_URL", "http://localhost:8000/sse")
