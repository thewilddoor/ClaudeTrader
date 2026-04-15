"""
Sandboxed Python script execution tool.

IMPORTANT: Each Letta-registered function must be fully self-contained (imports,
helpers inlined) because Letta's upsert_from_function extracts only the function
body and runs it in an isolated sandbox with no access to module-level code.

NOTE: No nested function definitions allowed — Letta's parser will extract them
as separate tools. Use lambdas or module-level helpers for preexec_fn.
"""
from typing import Optional


def _set_resource_limits():
    """Set 256MB memory limit for sandboxed subprocess. Not registered with Letta."""
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    except Exception:
        pass


def run_script(
    code: str,
    timeout: int = 30,
    scripts_dir: Optional[str] = None,
) -> dict:
    """Execute a Python script in a sandboxed subprocess.

    Args:
        code: Python source code to execute as a string.
        timeout: Maximum execution time in seconds before the process is killed (default 30).
        scripts_dir: Path to the scripts directory added to PYTHONPATH (reads from SCRIPTS_DIR env var).

    Returns:
        dict: Execution result with 'stdout', 'stderr', 'returncode', and 'error' keys.
    """
    import subprocess
    import tempfile
    import os
    import sys

    scripts_dir = scripts_dir or os.environ.get("SCRIPTS_DIR", "/app/scripts")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={
                "PYTHONPATH": scripts_dir,
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
            },
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "error": result.stderr if result.returncode != 0 else None,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": "Script execution timeout"}
    except Exception as e:
        return {"stdout": "", "stderr": "", "returncode": -1, "error": str(e)}
    finally:
        os.unlink(tmp_path)
