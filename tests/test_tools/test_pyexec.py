import pytest
from scheduler.tools.pyexec import run_script


def test_run_script_returns_stdout():
    result = run_script("print('hello')")
    assert result["stdout"] == "hello\n"
    assert result["returncode"] == 0
    assert result["error"] is None


def test_run_script_captures_json_output():
    script = "import json; print(json.dumps({'rsi': 65.4}))"
    result = run_script(script)
    assert '"rsi": 65.4' in result["stdout"]


def test_run_script_handles_syntax_error():
    result = run_script("def broken(:")
    assert result["returncode"] != 0
    assert result["error"] is not None


def test_run_script_enforces_timeout():
    result = run_script("import time; time.sleep(60)", timeout=1)
    assert result["returncode"] != 0
    assert "timeout" in result["error"].lower()


def test_run_script_blocks_network_import():
    result = run_script("import socket; s = socket.create_connection(('8.8.8.8', 80))", timeout=3)
    # Should either fail or be blocked — socket may be importable but connection refused in sandbox
    # We verify the script doesn't silently succeed and exfiltrate data
    assert result["returncode"] != 0 or "refused" in (result["error"] or "").lower()
