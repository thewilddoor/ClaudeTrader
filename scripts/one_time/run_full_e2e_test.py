#!/usr/bin/env python3
"""
Full end-to-end session test runner with complete capture.
Runs all 4 trading sessions sequentially and captures every API interaction.

Usage (from VPS inside docker):
  docker compose exec -e PYTHONPATH=/app scheduler python scripts/one_time/run_full_e2e_test.py
"""

import json
import logging
import os
import sys
from datetime import datetime, date
from pathlib import Path
from zoneinfo import ZoneInfo

# Setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
OUTPUT_FILE = "/tmp/e2e_test_results.json"


def serialize_block(block) -> dict:
    """Serialize an Anthropic content block."""
    if hasattr(block, "text"):
        return {"type": "text", "text": block.text}
    elif hasattr(block, "name"):
        return {
            "type": "tool_use",
            "id": getattr(block, "id", None),
            "name": block.name,
            "input": block.input,
        }
    elif hasattr(block, "type"):
        return {"type": block.type, "raw": str(block)}
    return {"type": "unknown", "raw": str(block)}


def serialize_message(msg: dict) -> dict:
    """Serialize a message dict (may contain raw blocks or strings)."""
    content = msg.get("content")
    if isinstance(content, str):
        return {"role": msg.get("role"), "content": content}
    elif isinstance(content, list):
        serialized = []
        for item in content:
            if isinstance(item, dict):
                serialized.append(item)
            elif hasattr(item, "type"):
                serialized.append(serialize_block(item))
            else:
                serialized.append({"raw": str(item)})
        return {"role": msg.get("role"), "content": serialized}
    return msg


class CapturingClient:
    """Wraps Anthropic client to log every API call and response."""

    def __init__(self, real_client, session_captures: list):
        self._real = real_client
        self._captures = session_captures
        self.messages = self

    def create(self, **kwargs):
        call_num = len(self._captures) + 1

        # Serialize messages (conversation history)
        messages = kwargs.get("messages", [])
        serialized_messages = [serialize_message(m) for m in messages]

        # Make real API call
        response = self._real.messages.create(**kwargs)

        # Serialize response
        response_content = [serialize_block(b) for b in response.content]

        self._captures.append({
            "api_call": call_num,
            "stop_reason": response.stop_reason,
            "messages_sent": serialized_messages,
            "response_content": response_content,
            "usage": {
                "input_tokens": getattr(response.usage, "input_tokens", None),
                "output_tokens": getattr(response.usage, "output_tokens", None),
                "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", None),
                "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", None),
            } if hasattr(response, "usage") else None,
        })

        return response


def run_session_captured(session_type: str, prompt: str, session_captures: list) -> str:
    """Run a single session with full capture, return raw output."""
    import anthropic
    from scheduler.agent import AgentCore, build_system_prompt, TOOL_SCHEMAS, _execute_tool, MAX_TOOL_ITERATIONS, _extract_text
    from scheduler.memory import MemoryStore
    from scheduler.digester import SessionDigester
    import threading

    db_path = os.environ.get("TRADES_DB_PATH", "/data/trades/trades.db")
    memory = MemoryStore(db_path=db_path)
    real_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    capturing_client = CapturingClient(real_client, session_captures)

    # Build system prompt
    blocks = memory.read_all()
    system = build_system_prompt(blocks)

    messages = [{"role": "user", "content": prompt}]
    tool_call_log = []

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = capturing_client.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            max_tokens=8192,
            system=system,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            text = _extract_text(response)
            log_id = memory.log_session(session_type, date.today().isoformat(), text)
            # Run digest in background (non-blocking)
            digester = SessionDigester(api_key=os.environ["ANTHROPIC_API_KEY"])
            def _digest(lid, sn, t):
                try:
                    summary = digester.summarize(sn, t)
                    if summary:
                        memory.update_session_digest(lid, summary)
                except Exception as e:
                    log.warning(f"Digest failed: {e}")
            threading.Thread(target=_digest, args=(log_id, session_type, text), daemon=True).start()
            return text

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input

                # Execute the tool
                if tool_name == "update_memory_block":
                    block_name = tool_input.get("block_name", "")
                    value = tool_input.get("value", "")
                    if block_name == "strategy_doc":
                        result = {"error": "Direct write to strategy_doc is blocked. Use proposed_change."}
                    else:
                        memory.write(block_name, value)
                        result = {"status": "ok", "block": block_name}
                else:
                    result = _execute_tool(tool_name, tool_input)

                tool_call_log.append({
                    "iteration": iteration + 1,
                    "tool": tool_name,
                    "input": tool_input,
                    "result_summary": str(result)[:500],  # truncate large results
                    "result_full": result,
                })

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result) if not isinstance(result, str) else result,
                })

            messages.append({"role": "user", "content": tool_results})

    return "[MAX_ITERATIONS_EXCEEDED]"


def main():
    log.info("=" * 60)
    log.info("ClaudeTrading E2E Test Runner")
    log.info(f"Date: {date.today().isoformat()}")
    log.info("=" * 60)

    from scheduler.main import (
        _build_recent_context_str,
        _get_todays_trades,
        _read_and_clear_pending_feedback,
    )
    from scheduler.sessions import (
        build_pre_market_prompt,
        build_market_open_prompt,
        build_health_check_prompt,
        build_eod_reflection_prompt,
    )

    now = datetime.now(ET)
    today = now.strftime("%Y-%m-%d")
    results = []

    sessions_to_run = [
        ("pre_market", "PRE-MARKET SESSION"),
        ("market_open", "MARKET OPEN SESSION"),
        ("health_check", "HEALTH CHECK SESSION"),
        ("eod_reflection", "EOD REFLECTION SESSION"),
    ]

    for session_type, label in sessions_to_run:
        log.info(f"\n{'='*60}")
        log.info(f"Starting: {label}")
        log.info(f"{'='*60}")

        recent_context = _build_recent_context_str()

        if session_type == "pre_market":
            prompt = build_pre_market_prompt(
                date=today,
                market_opens_in="3h30m",
                recent_context=recent_context,
            )
        elif session_type == "market_open":
            prompt = build_market_open_prompt(
                date=today,
                time_et="09:30",
                recent_context=recent_context,
            )
        elif session_type == "health_check":
            prompt = build_health_check_prompt(
                date=today,
                recent_context=recent_context,
            )
        elif session_type == "eod_reflection":
            trades_today = _get_todays_trades()
            pending_feedback = _read_and_clear_pending_feedback()
            prompt = build_eod_reflection_prompt(
                date=today,
                trades_today=trades_today,
                recent_context=recent_context,
                pending_feedback=pending_feedback,
            )

        session_captures = []
        error = None
        raw_output = None
        parsed_output = None

        try:
            raw_output = run_session_captured(session_type, prompt, session_captures)
            log.info(f"Session {session_type} complete. Raw output length: {len(raw_output)}")

            # Try to parse JSON
            try:
                # Strip any markdown fences
                clean = raw_output.strip()
                if clean.startswith("```"):
                    clean = clean.split("```")[1]
                    if clean.startswith("json"):
                        clean = clean[4:]
                    clean = clean.strip()
                parsed_output = json.loads(clean)
            except json.JSONDecodeError as e:
                parsed_output = {"parse_error": str(e), "raw": raw_output[:2000]}

        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            log.error(f"Session {session_type} failed: {e}", exc_info=True)

        results.append({
            "session_type": session_type,
            "label": label,
            "timestamp": datetime.now(ET).isoformat(),
            "prompt": prompt,
            "prompt_length_chars": len(prompt),
            "api_calls": session_captures,
            "api_call_count": len(session_captures),
            "raw_output": raw_output,
            "parsed_output": parsed_output,
            "error": error,
        })

    # Write results
    output = {
        "test_run": {
            "date": today,
            "started_at": now.isoformat(),
            "completed_at": datetime.now(ET).isoformat(),
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        },
        "sessions": results,
    }

    Path(OUTPUT_FILE).write_text(json.dumps(output, indent=2, default=str))
    log.info(f"\nResults written to {OUTPUT_FILE}")
    log.info(f"Total API calls made: {sum(len(r['api_calls']) for r in results)}")
    log.info("E2E test complete.")


if __name__ == "__main__":
    main()
