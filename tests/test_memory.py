"""Tests for MemoryStore class."""
import sqlite3
import tempfile
import os
import pytest


@pytest.fixture
def mem_db(monkeypatch):
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    import scheduler.tools.sqlite as sqlite_mod
    original = sqlite_mod.DB_PATH
    sqlite_mod.DB_PATH = db_path
    sqlite_mod.bootstrap_db()
    sqlite_mod.bootstrap_memory_table({"strategy_doc": "initial_doc", "watchlist": "initial_wl"})
    sqlite_mod.DB_PATH = original
    from scheduler.memory import MemoryStore
    store = MemoryStore(db_path=db_path)
    yield store
    os.unlink(db_path)


def test_read_returns_initial_value(mem_db):
    assert mem_db.read("strategy_doc") == "initial_doc"


def test_read_returns_none_for_missing_key(mem_db):
    assert mem_db.read("nonexistent_key") is None


def test_write_updates_existing_key(mem_db):
    mem_db.write("strategy_doc", "updated_doc")
    assert mem_db.read("strategy_doc") == "updated_doc"


def test_write_creates_new_key(mem_db):
    mem_db.write("observations", "first observation")
    assert mem_db.read("observations") == "first observation"


def test_read_all_returns_all_keys(mem_db):
    result = mem_db.read_all()
    assert "strategy_doc" in result
    assert "watchlist" in result
    assert result["strategy_doc"] == "initial_doc"


def test_log_session_and_retrieve(mem_db):
    log_id = mem_db.log_session("pre_market", "2026-04-16", "session response text")
    assert isinstance(log_id, int)
    assert log_id > 0


def test_update_session_digest(mem_db):
    log_id = mem_db.log_session("eod_reflection", "2026-04-16", "response")
    mem_db.update_session_digest(log_id, "DECISIONS MADE:\n- Opened NVDA long")
    digests = mem_db.get_recent_digests(n=2)
    assert len(digests) == 1
    assert "NVDA" in digests[0]["digest"]


def test_get_recent_digests_returns_chronological_order(mem_db):
    id1 = mem_db.log_session("pre_market", "2026-04-16", "r1")
    mem_db.update_session_digest(id1, "digest one")
    id2 = mem_db.log_session("market_open", "2026-04-16", "r2")
    mem_db.update_session_digest(id2, "digest two")
    digests = mem_db.get_recent_digests(n=2)
    assert len(digests) == 2
    assert digests[0]["digest"] == "digest one"
    assert digests[1]["digest"] == "digest two"


def test_get_recent_digests_skips_null_digests(mem_db):
    mem_db.log_session("pre_market", "2026-04-16", "no digest yet")
    id2 = mem_db.log_session("market_open", "2026-04-16", "r2")
    mem_db.update_session_digest(id2, "only digest")
    digests = mem_db.get_recent_digests(n=5)
    assert len(digests) == 1
    assert digests[0]["digest"] == "only digest"
