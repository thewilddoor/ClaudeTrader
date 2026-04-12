import json
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import Mock

import scheduler.tools.sqlite as sqlite_mod
import scheduler.strategy_gate as gate_mod


@pytest.fixture
def db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "trades.db")
    monkeypatch.setattr(sqlite_mod, "DB_PATH", db_path)
    sqlite_mod.bootstrap_db()
    return db_path


@pytest.fixture
def feedback_path(tmp_path, monkeypatch):
    path = tmp_path / "pending_feedback.txt"
    monkeypatch.setattr(gate_mod, "PENDING_FEEDBACK_PATH", path)
    return path


def _insert_version(db_path: str, version: str = "v1", status: str = "confirmed"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO strategy_versions (version, status, doc_text, promote_after) VALUES (?, ?, 'doc', 20)",
        (version, status),
    )
    conn.commit()
    conn.close()


def _insert_closed_trade(db_path: str, r_multiple: float, outcome_pnl: float,
                          version: str = "v1", context: dict = None):
    conn = sqlite3.connect(db_path)
    ctx = json.dumps(context) if context else None
    conn.execute(
        """INSERT INTO trades
           (ticker, side, entry_price, size, setup_type, hypothesis_id, rationale,
            vix_at_entry, regime, r_multiple, outcome_pnl, strategy_version, context_json, closed_at)
           VALUES ('AAPL','buy',100,10,'momentum','H001','test',15.0,'bull',?,?,?,?,datetime('now'))""",
        (r_multiple, outcome_pnl, version, ctx),
    )
    conn.commit()
    conn.close()


# --- run_prescreen ---

def test_prescreen_blocks_when_removed_trades_are_profitable(db):
    _insert_version(db)
    # Passes filter (rsi < 65) — losing
    _insert_closed_trade(db, r_multiple=-0.5, outcome_pnl=-50, context={"rsi": 55.0})
    # Fails filter (rsi >= 65) — winning; would be removed
    _insert_closed_trade(db, r_multiple=1.5, outcome_pnl=150, context={"rsi": 70.0})

    result = gate_mod.run_prescreen("json_extract(context_json, '$.rsi') < 65")
    assert result.blocked is True
    assert result.avg_r_blocked > 0
    assert result.trades_evaluated == 1


def test_prescreen_allows_when_removed_trades_are_losing(db):
    _insert_version(db)
    _insert_closed_trade(db, r_multiple=1.5, outcome_pnl=150, context={"rsi": 55.0})
    _insert_closed_trade(db, r_multiple=-0.8, outcome_pnl=-80, context={"rsi": 70.0})

    result = gate_mod.run_prescreen("json_extract(context_json, '$.rsi') < 65")
    assert result.blocked is False
    assert result.avg_r_blocked < 0


def test_prescreen_not_blocked_when_no_context_json_data(db):
    _insert_version(db)
    # Trades without context_json excluded from pre-screen
    _insert_closed_trade(db, r_multiple=2.0, outcome_pnl=200, context=None)

    result = gate_mod.run_prescreen("json_extract(context_json, '$.rsi') < 65")
    assert result.blocked is False
    assert result.trades_evaluated == 0


def test_prescreen_raises_on_blocked_sql_keyword(db):
    with pytest.raises(gate_mod.StrategyGateError, match="blocked keyword"):
        gate_mod.run_prescreen("1=1; DROP TABLE trades")


# --- snapshot_baseline_metrics ---

def test_snapshot_returns_none_when_no_trades(db):
    _insert_version(db)
    metrics = gate_mod.snapshot_baseline_metrics()
    assert metrics["win_rate"] is None
    assert metrics["avg_r"] is None


def test_snapshot_computes_correct_metrics(db):
    _insert_version(db)
    _insert_closed_trade(db, r_multiple=2.0, outcome_pnl=200)   # win
    _insert_closed_trade(db, r_multiple=-1.0, outcome_pnl=-100)  # loss

    metrics = gate_mod.snapshot_baseline_metrics()
    assert metrics["win_rate"] == pytest.approx(50.0)
    assert metrics["avg_r"] == pytest.approx(0.5)


def test_snapshot_returns_none_when_no_strategy_version_row(db):
    # No rows in strategy_versions at all
    metrics = gate_mod.snapshot_baseline_metrics()
    assert metrics["win_rate"] is None
    assert metrics["avg_r"] is None


# --- apply_change ---

def test_apply_change_raises_when_probationary_already_active(db, feedback_path):
    _insert_version(db, "v1", "confirmed")
    _insert_version(db, "v2", "probationary")

    mock_agent = Mock()
    with pytest.raises(gate_mod.StrategyGateError, match="probationary"):
        gate_mod.apply_change(mock_agent, {"description": "new change", "new_strategy_doc": "doc"})

    assert feedback_path.exists()
    assert "v2" in feedback_path.read_text()


def test_apply_change_raises_when_prescreen_blocks(db, feedback_path):
    _insert_version(db, "v1", "confirmed")
    # Profitable trade that would be removed by the filter
    _insert_closed_trade(db, r_multiple=2.0, outcome_pnl=200, context={"rsi": 70.0})

    mock_agent = Mock()
    with pytest.raises(gate_mod.StrategyGateError, match="blocked"):
        gate_mod.apply_change(
            mock_agent,
            {
                "description": "Tighten RSI",
                "new_strategy_doc": "doc",
                "filter_sql": "json_extract(context_json, '$.rsi') < 65",
            },
        )
    mock_agent.update_memory_block.assert_not_called()


def test_apply_change_qualitative_sets_promote_after_20(db):
    _insert_version(db, "v1", "confirmed")
    mock_agent = Mock()

    result = gate_mod.apply_change(mock_agent, {"description": "Be patient", "new_strategy_doc": "new doc"})

    assert result["version"] == "v2"
    assert result["promote_after"] == 20
    assert result["description"] == "Be patient"


def test_apply_change_backtested_sets_promote_after_10(db):
    _insert_version(db, "v1", "confirmed")
    # Only losing trade removed — pre-screen passes
    _insert_closed_trade(db, r_multiple=-1.0, outcome_pnl=-100, context={"rsi": 70.0})

    mock_agent = Mock()
    result = gate_mod.apply_change(
        mock_agent,
        {
            "description": "Tighten RSI",
            "new_strategy_doc": "doc",
            "filter_sql": "json_extract(context_json, '$.rsi') < 65",
        },
    )
    assert result["promote_after"] == 10


def test_apply_change_increments_version_number(db):
    _insert_version(db, "v3", "confirmed")
    mock_agent = Mock()
    result = gate_mod.apply_change(mock_agent, {"description": "x", "new_strategy_doc": "d"})
    assert result["version"] == "v4"


def test_apply_change_writes_letta_before_inserting_db_row(db):
    _insert_version(db, "v1", "confirmed")
    mock_agent = Mock()
    mock_agent.update_memory_block.side_effect = RuntimeError("Letta unavailable")

    with pytest.raises(RuntimeError):
        gate_mod.apply_change(mock_agent, {"description": "x", "new_strategy_doc": "d"})

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT * FROM strategy_versions WHERE version='v2'").fetchone()
    conn.close()
    assert row is None  # DB row not inserted


def test_apply_change_strategy_doc_contains_metadata_block(db):
    _insert_version(db, "v1", "confirmed")
    mock_agent = Mock()

    gate_mod.apply_change(mock_agent, {"description": "x", "new_strategy_doc": "the body"})

    written_doc = mock_agent.update_memory_block.call_args[0][1]
    assert "version: v2" in written_doc
    assert "status: probationary" in written_doc
    assert "the body" in written_doc


def test_apply_change_inserts_probationary_row_in_db(db):
    _insert_version(db, "v1", "confirmed")
    mock_agent = Mock()
    gate_mod.apply_change(mock_agent, {"description": "x", "new_strategy_doc": "body"})

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM strategy_versions WHERE version='v2'").fetchone()
    conn.close()
    assert row is not None
    assert row["status"] == "probationary"
    assert "body" in row["doc_text"]
