#!/usr/bin/env python3
"""
Standalone integration test: sends a pre-market-style research prompt directly
to the Letta agent and captures every message (tool calls, tool returns,
assistant reasoning) to verify the full LLM ↔ tool pipeline works.

This does NOT go through the scheduler's run_session, so:
  - No Telegram notifications
  - No session logs written
  - No strategy gate processing

It DOES hit the live Letta agent (and therefore its memory), but the prompt
is framed as a diagnostic test so the agent won't alter its real state.

Usage:
  docker compose exec scheduler python scripts/one_time/test_tool_integration.py
"""

import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

from letta_client import Letta

LETTA_URL = os.environ.get("LETTA_SERVER_URL", "http://letta:8283")
AGENT_ID_FILE = Path("/app/state/.agent_id")
OUTPUT_DIR = Path("/app/logs/test_runs")


def get_agent_id() -> str:
    if AGENT_ID_FILE.exists():
        return AGENT_ID_FILE.read_text().strip()
    raise RuntimeError("No .agent_id file found — run bootstrap first")


def extract_messages(response) -> list[dict]:
    """Parse every message from a Letta response into a flat list of dicts."""
    results = []
    for msg in response.messages:
        entry = {"type": getattr(msg, "message_type", "unknown")}

        # Assistant text
        if hasattr(msg, "content") and msg.content:
            if isinstance(msg.content, str):
                entry["content"] = msg.content
            elif isinstance(msg.content, list):
                entry["content"] = " ".join(
                    getattr(p, "text", "") for p in msg.content
                )

        # Tool calls
        if hasattr(msg, "tool_call") and msg.tool_call:
            tc = msg.tool_call
            entry["tool_name"] = tc.name
            try:
                entry["tool_args"] = json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
            except (json.JSONDecodeError, TypeError):
                entry["tool_args"] = str(tc.arguments)

        # Tool returns
        if hasattr(msg, "tool_return") and msg.tool_return:
            raw = msg.tool_return
            try:
                entry["tool_return"] = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, TypeError):
                entry["tool_return"] = raw
            entry["tool_status"] = getattr(msg, "tool_return_status", None)

        # Reasoning / thinking
        if hasattr(msg, "reasoning") and msg.reasoning:
            entry["reasoning"] = msg.reasoning

        results.append(entry)
    return results


def run_test(test_name: str, prompt: str) -> dict:
    """Send a prompt, capture all messages, return structured result."""
    client = Letta(base_url=LETTA_URL)
    agent_id = get_agent_id()

    print(f"\n{'='*70}")
    print(f"TEST: {test_name}")
    print(f"{'='*70}")
    print(f"Prompt: {prompt[:200]}...")
    print(f"Sending to agent {agent_id}...")

    start = datetime.utcnow()
    response = client.agents.messages.create(
        agent_id=agent_id,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = (datetime.utcnow() - start).total_seconds()

    messages = extract_messages(response)

    # Print summary
    tool_calls = [m for m in messages if "tool_name" in m]
    tool_returns = [m for m in messages if "tool_return" in m]
    assistant_msgs = [m for m in messages if m["type"] == "assistant_message"]
    errors = [m for m in tool_returns if m.get("tool_status") == "error"]

    print(f"\nCompleted in {elapsed:.1f}s")
    print(f"  Tool calls:      {len(tool_calls)}")
    print(f"  Tool returns:     {len(tool_returns)}")
    print(f"  Errors:           {len(errors)}")
    print(f"  Assistant msgs:   {len(assistant_msgs)}")

    # Print tool call details
    if tool_calls:
        print(f"\n  Tool call sequence:")
        for tc in tool_calls:
            args_preview = json.dumps(tc.get("tool_args", {}))
            if len(args_preview) > 100:
                args_preview = args_preview[:100] + "..."
            print(f"    → {tc['tool_name']}({args_preview})")

    # Print errors
    if errors:
        print(f"\n  ⚠ ERRORS:")
        for e in errors:
            ret = e.get("tool_return", "")
            if isinstance(ret, str) and len(ret) > 300:
                ret = ret[:300] + "..."
            print(f"    {ret}")

    # Print final assistant message
    if assistant_msgs:
        last = assistant_msgs[-1].get("content", "")
        print(f"\n  Final assistant output (first 500 chars):")
        print(f"    {last[:500]}")

    return {
        "test_name": test_name,
        "prompt": prompt,
        "elapsed_seconds": elapsed,
        "total_messages": len(messages),
        "tool_calls": len(tool_calls),
        "tool_returns": len(tool_returns),
        "errors": len(errors),
        "error_details": [m.get("tool_return") for m in errors],
        "messages": messages,
    }


def main():
    today = date.today().isoformat()
    results = []

    # ── Test 1: Individual tool checks ──────────────────────────────
    results.append(run_test(
        "tool_health_check",
        f"DIAGNOSTIC TEST — DO NOT alter memory blocks or watchlist. "
        f"This is an operator-initiated integration test on {today}. "
        f"Run these three tool calls and report the raw results:\n"
        f"1. alpaca_get_account() — report equity and buying power\n"
        f"2. trade_query('SELECT COUNT(*) as total_trades FROM trades') — report count\n"
        f"3. fmp_ohlcv('SPY', limit=2) — report latest 2 closes\n"
        f"Reply with a simple pass/fail table. Do NOT update any memory blocks."
    ))

    # ── Test 2: Pre-market research simulation ──────────────────────
    results.append(run_test(
        "pre_market_research_sim",
        f"DIAGNOSTIC TEST — DO NOT alter memory blocks or watchlist. "
        f"This is an operator integration test, not a real session. "
        f"Simulate a condensed pre-market research flow for {today}:\n"
        f"1. Use fmp_screener to find top 5 stocks by volume (limit=5)\n"
        f"2. Pick the top result and run fmp_ohlcv on it (limit=10)\n"
        f"3. Use run_script to calculate a simple RSI on that data — "
        f"write inline Python using pandas, print the result\n"
        f"4. Use serper_search to find recent news on that ticker\n"
        f"Report all results as a structured summary. "
        f"Do NOT update any memory blocks or propose changes."
    ))

    # ── Test 3: Order placement dry-run (read-only) ─────────────────
    results.append(run_test(
        "execution_readiness",
        f"DIAGNOSTIC TEST — DO NOT place any real orders or alter memory. "
        f"This is an operator integration test, not a real session. "
        f"Verify execution readiness:\n"
        f"1. alpaca_get_account() — confirm account is active and report buying power\n"
        f"2. alpaca_get_positions() — list any open positions\n"
        f"3. alpaca_list_orders(status='open') — list any open orders\n"
        f"4. hypothesis_log('DIAG-001', 'formed', 'Integration test hypothesis — safe to delete')\n"
        f"5. trade_query('SELECT * FROM hypothesis_log WHERE hypothesis_id = \"DIAG-001\"') — confirm it was written\n"
        f"Report all results. Do NOT update memory blocks."
    ))

    # ── Write combined output ───────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    output_file = OUTPUT_DIR / f"integration_test_{timestamp}.json"
    output_file.write_text(json.dumps(results, indent=2, default=str))

    # ── Final summary ──────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("INTEGRATION TEST SUMMARY")
    print(f"{'='*70}")
    total_errors = sum(r["errors"] for r in results)
    for r in results:
        status = "✅ PASS" if r["errors"] == 0 else f"❌ FAIL ({r['errors']} errors)"
        print(f"  {r['test_name']:30s} {status:20s} ({r['tool_calls']} calls, {r['elapsed_seconds']:.1f}s)")

    print(f"\n  Total errors: {total_errors}")
    print(f"  Output saved: {output_file}")

    if total_errors > 0:
        print("\n  ⚠ Some tests had errors — check the output file for details.")
        sys.exit(1)
    else:
        print("\n  All tests passed. LLM ↔ tool integration is working.")
        sys.exit(0)


if __name__ == "__main__":
    main()
