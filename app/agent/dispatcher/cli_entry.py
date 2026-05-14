import argparse
import json
import sys
from typing import Any, Dict

from app.agent.dispatcher.cli_actions import run_cli_action


def _configure_stdio_utf8() -> None:
    """
    Force UTF-8 stdio for CLI subprocess output, especially on Windows where
    default code page (e.g. gbk) may fail to encode characters like BOM.
    """
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    _configure_stdio_utf8()

    parser = argparse.ArgumentParser(prog="agent-dispatcher-cli")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--action", required=True)
    run_parser.add_argument("--params", required=True, help="JSON object")

    args = parser.parse_args()

    if not args.cmd == "run":
        _print_json({"ok": False, "error": f"unsupported cmd: {args.cmd}"})
        return 2

    try:
        params = json.loads(args.params)
    except Exception as e:
        _print_json({"ok": False, "error": f"invalid params json: {e}"})
        return 2

    if not isinstance(params, dict):
        _print_json({"ok": False, "error": "params must be a json object"})
        return 2

    try:
        result = run_cli_action(action=args.action, params=params)
        _print_json({"ok": True, "result": result})
        return 0
    except Exception as e:
        _print_json({"ok": False, "error": str(e)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
