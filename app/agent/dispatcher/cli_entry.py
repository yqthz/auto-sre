import argparse
import json
from typing import Any, Dict

from app.agent.dispatcher.cli_actions import run_cli_action


def _print_json(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
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
