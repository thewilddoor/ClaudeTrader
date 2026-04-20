# tests/test_tools/test_sqlite.py
import json
import sqlite3
import pytest
import scheduler.tools.sqlite as sqlite_module
from scheduler.tools.sqlite import bootstrap_db, trade_open, trade_close, trade_query


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


def test_trade_close_stamps_exit_fields(db):
    result = trade_open(
        ticker="NVDA", side="buy", entry_price=875.50, size=10.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="Breakout", vix_at_entry=18.5, regime="bull_low_vol",
    )
    trade_id = result["trade_id"]
    trade_close(
        trade_id=trade_id,
        exit_price=910.00,
        exit_reason="hit_target",
        outcome_pnl=345.00,
        r_multiple=2.1,
    )
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    assert row["exit_price"] == 910.00
    assert row["outcome_pnl"] == 345.00
    assert row["r_multiple"] == 2.1
    assert row["exit_reason"] == "hit_target"
    assert row["closed_at"] is not None


def test_trade_close_raises_if_trade_not_found(db):
    with pytest.raises(ValueError, match="trade_id 9999 not found"):
        trade_close(
            trade_id=9999,
            exit_price=100.0,
            exit_reason="test",
            outcome_pnl=0.0,
            r_multiple=0.0,
        )


def test_trade_close_raises_if_already_closed(db):
    result = trade_open(
        ticker="TSLA", side="buy", entry_price=200.0, size=5.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="Gap up", vix_at_entry=22.0, regime="bull_high_vol",
    )
    trade_id = result["trade_id"]
    trade_close(trade_id=trade_id, exit_price=220.0, exit_reason="target", outcome_pnl=100.0, r_multiple=1.5)
    with pytest.raises(ValueError, match="already closed"):
        trade_close(trade_id=trade_id, exit_price=230.0, exit_reason="target", outcome_pnl=150.0, r_multiple=2.0)


def test_hypothesis_log_inserts_row(db):
    from scheduler.tools.sqlite import hypothesis_log
    result = hypothesis_log(
        hypothesis_id="H001",
        event_type="formed",
        body="Momentum setups outperform in high-VIX bull regimes",
    )
    assert "log_id" in result
    assert isinstance(result["log_id"], int)


def test_hypothesis_log_records_all_event_types(db):
    from scheduler.tools.sqlite import hypothesis_log
    for event_type in ("formed", "testing", "confirmed", "rejected", "refined"):
        hypothesis_log(hypothesis_id="H002", event_type=event_type, body=f"Event: {event_type}")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT event_type FROM hypothesis_log WHERE hypothesis_id = 'H002'"
    ).fetchall()
    conn.close()
    assert len(rows) == 5


def test_hypothesis_log_sets_logged_at(db):
    from scheduler.tools.sqlite import hypothesis_log
    hypothesis_log(hypothesis_id="H003", event_type="formed", body="test")
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT logged_at FROM hypothesis_log WHERE hypothesis_id = 'H003'"
    ).fetchone()
    conn.close()
    assert row["logged_at"] is not None


def test_trade_query_returns_list_of_dicts(db):
    trade_open(
        ticker="NVDA", side="buy", entry_price=875.50, size=10.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="Breakout", vix_at_entry=18.5, regime="bull_low_vol",
    )
    result = trade_query("SELECT ticker, entry_price FROM trades")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["ticker"] == "NVDA"
    assert result[0]["entry_price"] == 875.50


def test_trade_query_aggregation(db):
    for price, pnl, r in [(875.50, 345.0, 2.1), (200.0, -50.0, -0.5), (180.0, 200.0, 1.8)]:
        res = trade_open(
            ticker="X", side="buy", entry_price=price, size=1.0,
            setup_type="momentum", hypothesis_id="H001",
            rationale="test", vix_at_entry=20.0, regime="bull_low_vol",
        )
        trade_close(
            trade_id=res["trade_id"], exit_price=price + pnl,
            exit_reason="test", outcome_pnl=pnl, r_multiple=r,
        )
    result = trade_query(
        "SELECT AVG(r_multiple) as avg_r FROM trades WHERE setup_type = 'momentum' AND closed_at IS NOT NULL"
    )
    assert len(result) == 1
    assert abs(result[0]["avg_r"] - ((2.1 + -0.5 + 1.8) / 3)) < 0.001


def test_trade_query_blocks_insert(db):
    with pytest.raises(ValueError, match="read-only"):
        trade_query("INSERT INTO trades (ticker) VALUES ('X')")


def test_trade_query_blocks_drop(db):
    with pytest.raises(ValueError, match="read-only"):
        trade_query("DROP TABLE trades")


def test_trade_query_blocks_update(db):
    with pytest.raises(ValueError, match="read-only"):
        trade_query("UPDATE trades SET ticker = 'Y' WHERE id = 1")


def test_trade_query_empty_result(db):
    result = trade_query("SELECT * FROM trades WHERE ticker = 'NONEXISTENT'")
    assert result == []


def test_backup_creates_backup_file(db, monkeypatch, tmp_path):
    from scheduler.tools.sqlite import backup_trades_db
    backup_file = tmp_path / "trades.backup.db"
    monkeypatch.setattr(sqlite_module, "BACKUP_PATH", str(backup_file))
    backup_trades_db()
    assert backup_file.exists()


def test_backup_contains_same_data(db, monkeypatch, tmp_path):
    from scheduler.tools.sqlite import backup_trades_db
    backup_file = tmp_path / "trades.backup.db"
    monkeypatch.setattr(sqlite_module, "BACKUP_PATH", str(backup_file))
    trade_open(
        ticker="NVDA", side="buy", entry_price=875.50, size=10.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="Breakout", vix_at_entry=18.5, regime="bull_low_vol",
    )
    backup_trades_db()
    conn = sqlite3.connect(str(backup_file))
    rows = conn.execute("SELECT ticker FROM trades").fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "NVDA"


def test_bootstrap_creates_strategy_versions_table(db):
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()
    assert "strategy_versions" in tables


def test_bootstrap_adds_strategy_version_and_context_json_columns(db):
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    conn.close()
    assert "strategy_version" in cols
    assert "context_json" in cols


def test_bootstrap_is_idempotent_with_new_columns(db, monkeypatch):
    monkeypatch.setattr(sqlite_module, "DB_PATH", str(db))
    bootstrap_db()  # second call must not raise


def test_trade_open_stamps_strategy_version_from_table(db):
    # Seed a confirmed version row
    conn = sqlite3.connect(str(db))
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
        "VALUES ('v1', 'confirmed', 'doc', 20)"
    )
    conn.commit()
    conn.close()

    result = trade_open(
        ticker="AAPL", side="buy", entry_price=100.0, size=10.0,
        setup_type="momentum", hypothesis_id="H001", rationale="test",
        vix_at_entry=15.0, regime="bull",
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT strategy_version FROM trades WHERE id=?", (result["trade_id"],)
    ).fetchone()
    conn.close()
    assert row[0] == "v1"


def test_trade_open_stamps_none_when_strategy_versions_empty(db):
    result = trade_open(
        ticker="AAPL", side="buy", entry_price=100.0, size=10.0,
        setup_type="momentum", hypothesis_id="H001", rationale="test",
        vix_at_entry=15.0, regime="bull",
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT strategy_version FROM trades WHERE id=?", (result["trade_id"],)
    ).fetchone()
    conn.close()
    assert row[0] is None


def test_trade_open_stores_context_json(db):
    ctx = json.dumps({"rsi": 62.5, "adx": 28.0})
    result = trade_open(
        ticker="NVDA", side="buy", entry_price=500.0, size=5.0,
        setup_type="momentum", hypothesis_id="H002", rationale="test",
        vix_at_entry=18.0, regime="bull", context_json=ctx,
    )
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT context_json FROM trades WHERE id=?", (result["trade_id"],)
    ).fetchone()
    conn.close()
    assert json.loads(row[0]) == {"rsi": 62.5, "adx": 28.0}


def test_schema_has_alpaca_order_id_and_stop_order_id(db):
    conn = sqlite3.connect(str(db))
    cols = [row[1] for row in conn.execute("PRAGMA table_info(trades)").fetchall()]
    conn.close()
    assert "alpaca_order_id" in cols
    assert "stop_order_id" in cols


def _open_trade(db: str, ticker="NVDA", entry_price=100.0, size=10.0) -> int:
    """Helper: insert a minimal open trade row, return trade_id."""
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    cursor = conn.execute(
        "INSERT INTO trades (ticker, side, entry_price, size, setup_type, "
        "hypothesis_id, rationale, vix_at_entry, regime) "
        "VALUES (?, 'buy', ?, ?, 'momentum', 'H001', 'test', 15.0, 'bull_low_vol')",
        (ticker, entry_price, size),
    )
    conn.commit()
    trade_id = cursor.lastrowid
    conn.close()
    return trade_id


def test_trade_update_fill_writes_price_and_order_id(db):
    from scheduler.tools.sqlite import trade_update_fill
    trade_id = _open_trade(str(db))
    result = trade_update_fill(trade_id, 102.50, "alpaca-order-abc123")
    assert result == {"status": "ok", "trade_id": trade_id, "entry_price": 102.50}
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT entry_price, alpaca_order_id FROM trades WHERE id = ?", (trade_id,)).fetchone()
    conn.close()
    assert row[0] == 102.50
    assert row[1] == "alpaca-order-abc123"


def test_trade_update_fill_rejects_unknown_trade(db):
    from scheduler.tools.sqlite import trade_update_fill
    with pytest.raises(ValueError, match="not found"):
        trade_update_fill(999, 100.0, "order-xyz")


def test_trade_update_fill_rejects_already_closed_trade(db):
    from scheduler.tools.sqlite import trade_update_fill
    trade_id = _open_trade(str(db))
    conn = sqlite3.connect(str(db))
    conn.execute(
        "UPDATE trades SET closed_at = datetime('now'), exit_price = 105.0, "
        "exit_reason = 'hit_target', outcome_pnl = 50.0, r_multiple = 1.0 WHERE id = ?",
        (trade_id,),
    )
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="already closed"):
        trade_update_fill(trade_id, 102.0, "order-abc")


def test_trade_close_computes_pnl_server_side_long(db):
    """Long trade: entry 100, exit 110, size 10, stop_loss 95 → pnl=100, risk=50, r=2.0"""
    result = trade_open(
        ticker="AAPL", side="buy", entry_price=100.0, size=10.0,
        setup_type="momentum", hypothesis_id="H001",
        rationale="test", vix_at_entry=15.0, regime="bull",
        stop_loss=95.0,
    )
    trade_id = result["trade_id"]
    trade_close(trade_id, 110.0, "hit_target")

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT outcome_pnl, r_multiple FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(100.0)   # (110-100)*10
    assert row[1] == pytest.approx(2.0)     # 100 / ((100-95)*10)


def test_trade_close_computes_pnl_server_side_short(db):
    """Short trade: entry 100, exit 90, size 10, stop_loss 105 → pnl=100, risk=50, r=2.0"""
    result = trade_open(
        ticker="SPY", side="sell", entry_price=100.0, size=10.0,
        setup_type="mean_reversion", hypothesis_id="H002",
        rationale="test", vix_at_entry=15.0, regime="bear",
        stop_loss=105.0,
    )
    trade_id = result["trade_id"]
    trade_close(trade_id, 90.0, "hit_target")

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT outcome_pnl, r_multiple FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(100.0)   # (100-90)*10
    assert row[1] == pytest.approx(2.0)     # 100 / ((105-100)*10)


def test_trade_close_override_values_stored_when_provided(db):
    """If Claude provides outcome_pnl and r_multiple, those are used as-is."""
    result = trade_open(
        ticker="MSFT", side="buy", entry_price=100.0, size=10.0,
        setup_type="momentum", hypothesis_id="H003",
        rationale="test", vix_at_entry=15.0, regime="bull",
        stop_loss=95.0,
    )
    trade_id = result["trade_id"]
    trade_close(trade_id, 105.0, "manual", outcome_pnl=42.0, r_multiple=0.84)

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT outcome_pnl, r_multiple FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(42.0)
    assert row[1] == pytest.approx(0.84)


def test_trade_close_zero_risk_gives_r_zero(db):
    """No stop_loss set → risk = 0 → r_multiple = 0.0, not a division error."""
    result = trade_open(
        ticker="TSLA", side="buy", entry_price=100.0, size=5.0,
        setup_type="momentum", hypothesis_id="H004",
        rationale="test", vix_at_entry=15.0, regime="bull",
    )
    trade_id = result["trade_id"]
    trade_close(trade_id, 110.0, "hit_target")

    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT outcome_pnl, r_multiple FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    conn.close()
    assert row[0] == pytest.approx(50.0)
    assert row[1] == pytest.approx(0.0)
