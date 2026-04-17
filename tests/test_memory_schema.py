# tests/test_memory_schema.py
import sqlite3
import tempfile
import os
import pytest


def test_bootstrap_creates_memory_table():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        import scheduler.tools.sqlite as sqlite_mod
        original = sqlite_mod.DB_PATH
        sqlite_mod.DB_PATH = db_path
        sqlite_mod.bootstrap_db()
        sqlite_mod.bootstrap_memory_table({"strategy_doc": "initial"})
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM memory WHERE key='strategy_doc'").fetchone()
        assert row is not None
        assert row[0] == "initial"
        conn.close()
    finally:
        sqlite_mod.DB_PATH = original
        os.unlink(db_path)


def test_bootstrap_memory_table_is_idempotent():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        import scheduler.tools.sqlite as sqlite_mod
        original = sqlite_mod.DB_PATH
        sqlite_mod.DB_PATH = db_path
        sqlite_mod.bootstrap_db()
        sqlite_mod.bootstrap_memory_table({"key1": "val1"})
        sqlite_mod.bootstrap_memory_table({"key1": "SHOULD_NOT_OVERWRITE"})
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM memory WHERE key='key1'").fetchone()
        assert row[0] == "val1"
        conn.close()
    finally:
        sqlite_mod.DB_PATH = original
        os.unlink(db_path)


def test_bootstrap_creates_session_log_table():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        import scheduler.tools.sqlite as sqlite_mod
        original = sqlite_mod.DB_PATH
        sqlite_mod.DB_PATH = db_path
        sqlite_mod.bootstrap_db()
        sqlite_mod.bootstrap_memory_table({})
        conn = sqlite3.connect(db_path)
        conn.execute("SELECT id, session_name, date, raw_response, digest FROM session_log LIMIT 1")
        conn.close()
    finally:
        sqlite_mod.DB_PATH = original
        os.unlink(db_path)
