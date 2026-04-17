# scheduler/bootstrap.py
"""
One-time bootstrap. Seeds the SQLite DB, memory blocks, and strategy_versions v1.
Run manually: python -m scheduler.bootstrap
Idempotent — safe to run again on restart.
"""
import os
from pathlib import Path
from scheduler.tools.sqlite import (
    bootstrap_db as init_trades_db,
    bootstrap_memory_table,
    _connect as _db_connect,
)
from scheduler.agent import STATIC_PROMPT

AGENT_ID_FILE = Path("/app/state/.agent_id")

_INITIAL_MEMORY = {
    "strategy_doc": STATIC_PROMPT,
    "watchlist": "# Watchlist\nEmpty on bootstrap — populated during pre_market sessions.\n",
    "performance_snapshot": (
        '{"trades_total": 0, "win_rate_10": null, "win_rate_20": null, '
        '"avg_rr": null, "current_drawdown_pct": 0.0, '
        '"peak_equity": 50000.0, "current_equity": 50000.0, "pivot_alerts": []}'
    ),
    "today_context": "# Today's Context\nNot yet populated — will be written during pre_market session.\n",
    "observations": "# Observations\nNo observations yet.\n",
}


def bootstrap():
    print("Initialising SQLite trade store...")
    init_trades_db()
    print("Trade store ready.")

    print("Initialising memory and session_log tables...")
    bootstrap_memory_table(_INITIAL_MEMORY)
    print("Memory tables ready.")

    # Seed v1 row in strategy_versions — metadata block prepended for consistency
    _v1_metadata = (
        "## Version metadata\n"
        "version: v1\n"
        "status: confirmed\n"
        "promote_after: 20\n"
        "baseline_win_rate: null\n"
        "baseline_avg_r: null\n\n"
    )
    _v1_doc_text = _v1_metadata + STATIC_PROMPT
    db_conn = _db_connect()
    try:
        if db_conn.execute("SELECT version FROM strategy_versions WHERE version='v1'").fetchone() is None:
            db_conn.execute(
                "INSERT INTO strategy_versions (version, status, doc_text, promote_after) "
                "VALUES ('v1', 'confirmed', ?, 20)",
                (_v1_doc_text,),
            )
            db_conn.commit()
            print("Seeded strategy_versions with v1 (confirmed).")
        else:
            print("strategy_versions v1 already exists — skipping seed.")
    finally:
        db_conn.close()

    print("Bootstrap complete.")


if __name__ == "__main__":
    bootstrap()
