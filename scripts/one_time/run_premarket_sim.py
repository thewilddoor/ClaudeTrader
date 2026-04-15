#!/usr/bin/env python3
"""
Run a full simulated pre-market session with extended timeout,
capturing every message (reasoning, tool calls, tool returns, assistant text).
"""

import json
import httpx
from datetime import datetime
from pathlib import Path

from letta_client import Letta

LETTA_URL = "http://letta:8283"
AGENT_ID = Path("/app/state/.agent_id").read_text().strip()
OUTPUT_DIR = Path("/app/logs/test_runs")

prompt = """SESSION: pre_market | DATE: 2026-04-16 | MARKET_OPENS_IN: 3h30m

OPERATOR NOTE: This is a LIVE pre-market session. Treat this as a real session.
Do your full pre-market research workflow:
- Assess market regime (use run_script with market_regime_detector or manual analysis)
- Screen and filter candidates using fmp_screener
- Run technical analysis (OHLCV + indicators via run_script) on promising tickers
- Check news/catalysts via fmp_news or serper_search
- Build your watchlist with planned entry/stop/target levels
- Update today_context and watchlist memory blocks

Execute your complete research process. Take your time and be thorough."""


def main():
    # Use a 10-minute timeout for the full research session
    http_client = httpx.Client(timeout=httpx.Timeout(600.0))
    client = Letta(base_url=LETTA_URL, http_client=http_client)

    print(f"Sending pre-market session to agent {AGENT_ID}...")
    print(f"Timeout: 600s. This will take a few minutes.\n")

    start = datetime.utcnow()
    response = client.agents.messages.create(
        agent_id=AGENT_ID,
        messages=[{"role": "user", "content": prompt}],
    )
    elapsed = (datetime.utcnow() - start).total_seconds()

    # Parse every message
    output = []
    for msg in response.messages:
        t = getattr(msg, "message_type", "unknown")
        entry = {"type": t}

        if hasattr(msg, "reasoning") and msg.reasoning:
            entry["reasoning"] = msg.reasoning

        if hasattr(msg, "content") and msg.content:
            if isinstance(msg.content, str):
                entry["content"] = msg.content
            elif isinstance(msg.content, list):
                entry["content"] = " ".join(
                    getattr(p, "text", "") for p in msg.content
                )

        if hasattr(msg, "tool_call") and msg.tool_call:
            tc = msg.tool_call
            entry["tool_name"] = tc.name
            try:
                entry["tool_args"] = (
                    json.loads(tc.arguments)
                    if isinstance(tc.arguments, str)
                    else tc.arguments
                )
            except (json.JSONDecodeError, TypeError):
                entry["tool_args"] = str(tc.arguments)

        if hasattr(msg, "tool_return") and msg.tool_return:
            entry["tool_return"] = msg.tool_return
            entry["tool_status"] = getattr(msg, "tool_return_status", None)

        output.append(entry)

    # Save
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    outfile = OUTPUT_DIR / f"full_premarket_{ts}.json"
    outfile.write_text(json.dumps(output, indent=2, default=str))

    # Print chronological trace
    print(f"Session completed in {elapsed:.1f}s")
    print(f"Total messages: {len(output)}\n")
    print("=" * 80)
    print("CHRONOLOGICAL TRACE")
    print("=" * 80)

    step = 0
    for entry in output:
        t = entry["type"]

        if t == "reasoning_message" and entry.get("reasoning"):
            step += 1
            text = entry["reasoning"]
            # Truncate very long reasoning
            if len(text) > 800:
                text = text[:800] + "..."
            print(f"\n[{step}] 🧠 THINKING:")
            print(f"    {text}")

        elif "tool_name" in entry:
            step += 1
            args = json.dumps(entry.get("tool_args", {}), indent=None)
            if len(args) > 300:
                args = args[:300] + "..."
            print(f"\n[{step}] 🔧 TOOL CALL: {entry['tool_name']}")
            print(f"    Args: {args}")

        elif "tool_return" in entry:
            ret = entry["tool_return"]
            status = entry.get("tool_status", "ok")
            marker = "❌" if status == "error" else "✅"
            if isinstance(ret, str) and len(ret) > 500:
                ret = ret[:500] + "..."
            elif not isinstance(ret, str):
                ret = json.dumps(ret, indent=None, default=str)
                if len(ret) > 500:
                    ret = ret[:500] + "..."
            print(f"    {marker} Return: {ret}")

        elif t == "assistant_message" and entry.get("content"):
            step += 1
            text = entry["content"]
            print(f"\n[{step}] 💬 ASSISTANT:")
            for line in text.split("\n"):
                print(f"    {line}")

    # Stats
    tool_calls = [e for e in output if "tool_name" in e]
    tool_names = [e["tool_name"] for e in tool_calls]
    errors = [e for e in output if e.get("tool_status") == "error"]
    reasoning = [e for e in output if e.get("reasoning")]

    print(f"\n{'=' * 80}")
    print("STATS")
    print(f"{'=' * 80}")
    print(f"  Elapsed:         {elapsed:.1f}s")
    print(f"  Reasoning steps: {len(reasoning)}")
    print(f"  Tool calls:      {len(tool_calls)}")
    print(f"  Errors:          {len(errors)}")
    print(f"  Tools used:      {', '.join(dict.fromkeys(tool_names))}")
    print(f"  Output file:     {outfile}")


if __name__ == "__main__":
    main()
