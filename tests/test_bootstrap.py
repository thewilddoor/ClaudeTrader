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
    sqlite_mod.bootstrap_db()
    return {"db_path": db_path, "agent_id_path": agent_id_path, "tmp_path": tmp_path}


def test_bootstrap_seeds_v1_row_in_strategy_versions(fresh_env, monkeypatch):
    from scheduler.agent import STATIC_PROMPT

    monkeypatch.setattr(sqlite_mod, "DB_PATH", fresh_env["db_path"])

    from scheduler.bootstrap import bootstrap
    bootstrap()

    conn = sqlite3.connect(fresh_env["db_path"])
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM strategy_versions WHERE version='v1'").fetchone()
    conn.close()

    assert row is not None
    assert row["status"] == "confirmed"
    assert STATIC_PROMPT in row["doc_text"]
    assert "## Version metadata" in row["doc_text"]
    assert "version: v1" in row["doc_text"]
