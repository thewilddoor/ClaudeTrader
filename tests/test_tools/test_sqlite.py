# tests/test_tools/test_sqlite.py
import sqlite3
import pytest
import scheduler.tools.sqlite as sqlite_module
from scheduler.tools.sqlite import bootstrap_db, trade_open


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Temporary isolated DB with schema created."""
    db_file = tmp_path / "trades.db"
    monkeypatch.setattr(sqlite_module, "DB_PATH", str(db_file))
    bootstrap_db()
    return db_file


def test_bootstrap_creates_trades_table(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_bootstrap_creates_hypothesis_log_table(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='hypothesis_log'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_bootstrap_is_idempotent(db, monkeypatch):
    # Calling bootstrap_db a second time should not raise
    monkeypatch.setattr(sqlite_module, "DB_PATH", str(db))
    bootstrap_db()  # second call — must not raise


def test_bootstrap_sets_wal_mode(db):
    conn = sqlite3.connect(str(db))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_trade_open_returns_trade_id(db):
    result = trade_open(
        ticker="NVDA",
        side="buy",
        entry_price=875.50,
        size=10.0,
        setup_type="momentum",
        hypothesis_id="H001",
        rationale="Breaking out of 3-week base on volume",
        vix_at_entry=18.5,
        regime="bull_low_vol",
        stop_loss=855.00,
        take_profit=940.00,
    )
    assert "trade_id" in result
    assert isinstance(result["trade_id"], int)
    assert result["trade_id"] >= 1


def test_trade_open_persists_row(db):
    trade_open(
        ticker="TSLA",
        side="buy",
        entry_price=200.00,
        size=5.0,
        setup_type="momentum",
        hypothesis_id="H002",
        rationale="Gap up on earnings beat",
        vix_at_entry=22.0,
        regime="bull_high_vol",
        stop_loss=190.00,
        take_profit=225.00,
    )
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM trades WHERE ticker = 'TSLA'").fetchone()
    conn.close()
    assert row is not None
    assert row["entry_price"] == 200.00
    assert row["stop_loss"] == 190.00
    assert row["take_profit"] == 225.00
    assert row["closed_at"] is None


def test_trade_open_without_stops(db):
    result = trade_open(
        ticker="AAPL",
        side="sell",
        entry_price=180.00,
        size=3.0,
        setup_type="mean_reversion",
        hypothesis_id="H003",
        rationale="Extended above VWAP",
        vix_at_entry=15.0,
        regime="range_low_vol",
    )
    assert "trade_id" in result
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM trades WHERE ticker = 'AAPL'").fetchone()
    conn.close()
    assert row["stop_loss"] is None
    assert row["take_profit"] is None


def test_trade_open_raises_if_no_db(tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite_module, "DB_PATH", str(tmp_path / "missing.db"))
    with pytest.raises(RuntimeError, match="run bootstrap first"):
        trade_open(
            ticker="X",
            side="buy",
            entry_price=1.0,
            size=1.0,
            setup_type="test",
            hypothesis_id="H000",
            rationale="test",
            vix_at_entry=20.0,
            regime="test",
        )
