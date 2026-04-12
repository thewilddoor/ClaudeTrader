import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

import scheduler.tools.sqlite as sqlite_mod


@pytest.fixture
def fresh_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trades.db")
    agent_id_path = tmp_path / ".agent_id"
    monkeypatch.setattr(sqlite_mod, "DB_PATH", db_path)
    monkeypatch.setenv("LETTA_AGENT_NAME", "test_trader")
    monkeypatch.setenv("LETTA_SERVER_URL", "http://localhost:8283")
    sqlite_mod.bootstrap_db()
    return {"db_path": db_path, "agent_id_path": agent_id_path, "tmp_path": tmp_path}


def test_bootstrap_seeds_v1_row_in_strategy_versions(fresh_env, monkeypatch):
    from scheduler.agent import INITIAL_STRATEGY_DOC

    mock_agent = MagicMock()
    mock_agent.agent_id = "mock-agent-123"

    monkeypatch.setattr("scheduler.bootstrap.AGENT_ID_FILE", fresh_env["agent_id_path"])
    monkeypatch.setattr("scheduler.bootstrap.LettaTraderAgent.create_new",
                        staticmethod(lambda *a, **kw: mock_agent))
    monkeypatch.setattr("scheduler.bootstrap.register_all_tools", lambda *a: [])
    monkeypatch.setattr("scheduler.bootstrap.attach_alpaca_mcp", lambda *a: False)

    from scheduler.bootstrap import bootstrap
    bootstrap()

    conn = sqlite3.connect(fresh_env["db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM strategy_versions WHERE version='v1'").fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "confirmed"
    assert INITIAL_STRATEGY_DOC in row["doc_text"]
    assert "## Version metadata" in row["doc_text"]
    assert "version: v1" in row["doc_text"]
