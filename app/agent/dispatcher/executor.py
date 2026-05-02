import os
import time
from typing import Any, Dict

from app.agent.dispatcher.cli_runner import run_via_cli
from app.agent.dispatcher.policy import evaluate_action

MAX_BACKOFF_SECONDS = 5.0


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


def _should_retry(error_kind: str, attempt: int, max_retries: int, retry_on_kinds: list[str]) -> bool:
    if attempt > max_retries:
        return False
    return error_kind in set(retry_on_kinds)


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

    attempt = 0
    retry_history = []
    last_cli_result = None

    while True:
        cli_result = run_via_cli(action=action, params=params, timeout_seconds=meta.timeout_seconds)
        last_cli_result = cli_result

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
                "attempts": attempt + 1,
                "last_error_kind": "",
                "retry_history": retry_history,
            }

        attempt += 1
        err_kind = str(cli_result.get("kind") or "unknown")
        err_text = str(cli_result.get("error") or "")
        retry_history.append(
            {
                "attempt": attempt,
                "error_kind": err_kind,
                "error": err_text[:260],
                "timeout_seconds": meta.timeout_seconds,
            }
        )

        if _should_retry(
            error_kind=err_kind,
            attempt=attempt,
            max_retries=meta.max_retries,
            retry_on_kinds=meta.retry_on_kinds,
        ):
            backoff_seconds = min(
                MAX_BACKOFF_SECONDS,
                meta.retry_backoff_seconds * (meta.retry_backoff_multiplier ** (attempt - 1)),
            )
            if backoff_seconds > 0:
                time.sleep(backoff_seconds)
            continue

        break

    fallback_reason = "cli_fallback:{kind}: {error}".format(
        kind=str((last_cli_result or {}).get("kind") or "unknown"),
        error=str((last_cli_result or {}).get("error") or "")[:260],
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
            "attempts": attempt,
            "last_error_kind": str((last_cli_result or {}).get("kind") or "unknown"),
            "retry_history": retry_history,
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
            "attempts": attempt,
            "last_error_kind": str((last_cli_result or {}).get("kind") or "unknown"),
            "retry_history": retry_history,
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
            "attempts": attempt,
            "last_error_kind": str((last_cli_result or {}).get("kind") or "unknown"),
            "retry_history": retry_history,
        }
