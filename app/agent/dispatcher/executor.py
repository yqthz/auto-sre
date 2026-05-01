import os
from typing import Any, Dict

from app.agent.dispatcher.cli_runner import run_via_cli
from app.agent.dispatcher.policy import evaluate_action


def _legacy_fallback_enabled_for_action(action: str) -> bool:
    """
    Gradual fallback shutdown strategy:
    - DISPATCHER_ENABLE_LEGACY_FALLBACK=0 (default) => fallback disabled globally
    - DISPATCHER_ENABLE_LEGACY_FALLBACK=1 => fallback enabled
      * if DISPATCHER_FALLBACK_ACTION_ALLOWLIST is empty: allow all actions
      * else only allow listed actions
    """
    if os.getenv("DISPATCHER_ENABLE_LEGACY_FALLBACK", "0") != "1":
        return False

    allowlist_raw = os.getenv("DISPATCHER_FALLBACK_ACTION_ALLOWLIST", "")
    allowlist = {item.strip() for item in allowlist_raw.split(",") if item.strip()}
    if not allowlist:
        return True

    return action in allowlist


def dispatch_action(action: str, params: Dict[str, Any], user_role: str, mode: str) -> Dict[str, Any]:
    decision = evaluate_action(action=action, params=params, user_role=user_role, mode=mode)

    if decision.action_meta is None:
        return {
            "status": "denied",
            "action": action,
            "risk_level": "unknown",
            "requires_approval": False,
            "reason": decision.reason,
            "result": {},
            "error": decision.reason,
            "execution_backend": "none",
        }

    meta = decision.action_meta
    if decision.status == "denied":
        return {
            "status": "denied",
            "action": action,
            "risk_level": meta.risk_level,
            "requires_approval": meta.requires_approval,
            "reason": decision.reason,
            "result": {},
            "error": decision.reason,
            "execution_backend": "none",
        }

    cli_result = run_via_cli(action=action, params=params)
    if cli_result.get("ok"):
        return {
            "status": "executed",
            "action": action,
            "risk_level": meta.risk_level,
            "requires_approval": meta.requires_approval,
            "reason": "",
            "result": cli_result.get("result"),
            "error": "",
            "execution_backend": "cli",
        }

    fallback_reason = "cli_fallback:{kind}: {error}".format(
        kind=str(cli_result.get("kind") or "unknown"),
        error=str(cli_result.get("error") or "")[:260],
    )

    if not _legacy_fallback_enabled_for_action(action):
        return {
            "status": "failed",
            "action": action,
            "risk_level": meta.risk_level,
            "requires_approval": meta.requires_approval,
            "reason": fallback_reason,
            "result": {},
            "error": fallback_reason,
            "execution_backend": "cli_failed",
        }

    try:
        result = meta.fn(**params)
        return {
            "status": "executed",
            "action": action,
            "risk_level": meta.risk_level,
            "requires_approval": meta.requires_approval,
            "reason": fallback_reason,
            "result": result,
            "error": "",
            "execution_backend": "legacy_fallback",
        }
    except Exception as e:
        return {
            "status": "failed",
            "action": action,
            "risk_level": meta.risk_level,
            "requires_approval": meta.requires_approval,
            "reason": str(e),
            "result": {},
            "error": str(e),
            "execution_backend": "failed",
        }
