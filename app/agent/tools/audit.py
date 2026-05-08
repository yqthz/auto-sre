from __future__ import annotations

from typing import Any, Dict, Optional

from app.agent.approval_policy import tool_approval_profile
from app.storage import append_audit
from app.utils.format_utils import now_iso


def audit_tool_event(
    event_type: str,
    *,
    tool: str,
    user_id: str,
    user_role: str,
    mode: str,
    thread_id: str | None = None,
    trace_run_id: str | None = None,
    tool_call_id: str | None = None,
    args: dict | None = None,
    result: str | dict | None = None,
    error: str | None = None,
    status: str | None = None,
    extra: Dict[str, Any] | None = None,
) -> None:
    tool_args = args or {}
    profile_args = tool_args
    if tool == "dispatch_tool":
        profile_args = {
            "action": tool_args.get("action"),
            "params": tool_args.get("params"),
        }
    profile = tool_approval_profile(tool, profile_args)

    payload: Dict[str, Any] = {
        "timestamp": now_iso(),
        "event": event_type,
        "tool": tool,
        "args": tool_args,
        "user_id": str(user_id),
        "user_role": str(user_role),
        "mode": str(mode),
        "tool_permission": profile.get("permission"),
        "risk_level": profile.get("risk_level"),
        "requires_approval": profile.get("requires_approval"),
    }
    if thread_id:
        payload["thread_id"] = thread_id
    if trace_run_id:
        payload["trace_run_id"] = trace_run_id
    if tool_call_id:
        payload["tool_call_id"] = tool_call_id
    if result is not None:
        payload["result"] = result
    if error:
        payload["error"] = error
    if status:
        payload["status"] = status
    if extra:
        payload["extra"] = extra

    append_audit(payload)
