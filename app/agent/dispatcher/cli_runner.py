import json
import subprocess
import sys
from typing import Any, Dict

from app.agent.dispatcher.cli_actions import has_cli_handler

DEFAULT_TIMEOUT_SECONDS = 10
MAX_STDERR_CHARS = 1000


def run_via_cli(action: str, params: Dict[str, Any], timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Run one dispatcher action via structured CLI process."""
    if not has_cli_handler(action):
        return {
            "ok": False,
            "error": f"no cli handler for action: {action}",
            "kind": "unsupported",
        }

    cmd = [
        sys.executable,
        "-m",
        "app.agent.dispatcher.cli_entry",
        "run",
        "--action",
        action,
        "--params",
        json.dumps(params, ensure_ascii=False),
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"cli timeout after {timeout_seconds}s",
            "kind": "timeout",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"cli spawn error: {e}",
            "kind": "spawn_error",
        }

    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()

    if not stdout:
        return {
            "ok": False,
            "error": f"empty cli stdout, exit={proc.returncode}, stderr={stderr[:MAX_STDERR_CHARS]}",
            "kind": "invalid_output",
        }

    try:
        payload = json.loads(stdout)
    except Exception as e:
        return {
            "ok": False,
            "error": f"invalid cli json output: {e}; raw={stdout[:MAX_STDERR_CHARS]}",
            "kind": "invalid_output",
        }

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "error": "cli output must be json object",
            "kind": "invalid_output",
        }

    if not (proc.returncode == 0 and payload.get("ok")):
        return {
            "ok": False,
            "error": str(payload.get("error") or stderr[:MAX_STDERR_CHARS] or "cli command failed"),
            "kind": "cli_failed",
        }

    return {
        "ok": True,
        "result": payload.get("result"),
        "kind": "success",
    }
