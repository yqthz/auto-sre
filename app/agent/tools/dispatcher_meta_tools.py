import json
import time
from typing import Any, Dict, Optional

from app.agent.approval_policy import tool_approval_profile
from app.agent.dispatcher.discovery import cli_list_payload, cli_tool_doc_payload
from app.agent.dispatcher.executor import dispatch_action
from app.agent.tools.security import register_tool
from app.core.logger import logger
from app.storage import append_audit
from app.utils.format_utils import now_iso
from langchain_core.runnables import RunnableConfig

# _DISCOVERY_TTL_SECONDS = 600
# _MAX_LIST_CALLS_PER_ROUND = 1
# _MAX_DOC_CALLS_PER_ROUND = 2

# key -> session
# session 缓存了 cli_list 和 cli_tool_doc 的信息
# _DISCOVERY_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _context_from_config(config: Optional[RunnableConfig]) -> tuple[str, str, str, str, str]:
    """从 config 中提取 user_id, user_role, mode, thread_id, trace_run_id"""
    cfg = dict(config or {})
    configurable = dict(cfg.get("configurable") or {})
    user_id = str(configurable.get("user_id") or "unknown")
    user_role = str(configurable.get("user_role") or "viewer")
    mode = str(configurable.get("mode") or "manual")
    thread_id = str(configurable.get("thread_id") or "global")
    trace_run_id = str(configurable.get("trace_run_id") or "")
    return user_id, user_role, mode, thread_id, trace_run_id


# def _session_key(user_role: str, mode: str, thread_id: str) -> str:
#     return f"{thread_id}:{user_role}:{mode}"


# def _new_session(now_ts: float) -> Dict[str, Any]:
#     return {
#         "expires_at": now_ts + _DISCOVERY_TTL_SECONDS,
#         "list_cache": None,
#         "doc_cache": {},
#         "round_list_calls": 0,
#         "round_doc_calls": 0,
#         "doc_actions_viewed": set(),
#         "discovery_chars_this_round": 0,
#         "total_dispatch": 0,
#         "dispatch_with_doc": 0,
#     }


# def _get_session(user_role: str, mode: str, thread_id: str) -> Dict[str, Any]:
#     now_ts = time.time()
#     key = _session_key(user_role=user_role, mode=mode, thread_id=thread_id)
#     current = _DISCOVERY_SESSIONS.get(key)
#     if current is None or current.get("expires_at", 0) <= now_ts:
#         current = _new_session(now_ts)
#         _DISCOVERY_SESSIONS[key] = current
#     return current


# def _json_size(payload: Dict[str, Any]) -> int:
#     return len(json.dumps(payload, ensure_ascii=False))


# def _discovery_budget_error(kind: str) -> Dict[str, Any]:
#     return {
#         "error": "discovery_budget_exceeded",
#         "detail": f"{kind} budget exceeded in current round",
#         "allowed": {
#             "cli_list": _MAX_LIST_CALLS_PER_ROUND,
#             "cli_tool_doc": _MAX_DOC_CALLS_PER_ROUND,
#         },
#     }


# def _discovery_meta(session: Dict[str, Any], cache_hit: bool) -> Dict[str, Any]:
#     total_dispatch = int(session.get("total_dispatch", 0))
#     dispatch_with_doc = int(session.get("dispatch_with_doc", 0))
#     hit_rate = (dispatch_with_doc / total_dispatch) if total_dispatch > 0 else 0.0

#     return {
#         "cache_hit": cache_hit,
#         "ttl_seconds": _DISCOVERY_TTL_SECONDS,
#         "round_budget": {
#             "cli_list": _MAX_LIST_CALLS_PER_ROUND,
#             "cli_tool_doc": _MAX_DOC_CALLS_PER_ROUND,
#         },
#         "round_usage": {
#             "cli_list": int(session.get("round_list_calls", 0)),
#             "cli_tool_doc": int(session.get("round_doc_calls", 0)),
#         },
#         "discovery_chars_this_round": int(session.get("discovery_chars_this_round", 0)),
#         "doc_hit_rate": round(hit_rate, 4),
#     }


# def _reset_round_state(session: Dict[str, Any]) -> None:
#     session["round_list_calls"] = 0
#     session["round_doc_calls"] = 0
#     session["doc_actions_viewed"] = set()
#     session["discovery_chars_this_round"] = 0


@register_tool(
    name="cli_list",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["dispatcher"],
    requires_approval=False,
)
def cli_list(config: Optional[RunnableConfig] = None) -> str:
    """List available tool groups and actions for current session."""
    _user_id, user_role, mode, _thread_id, _trace_run_id = _context_from_config(config)
    payload = cli_list_payload(user_role=user_role, mode=mode)
    return json.dumps(payload, ensure_ascii=False)


@register_tool(
    name="cli_tool_doc",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["dispatcher"],
    requires_approval=False,
)
def cli_tool_doc(tool: str, config: Optional[RunnableConfig] = None) -> str:
    """Get minimal structured doc for a tool group."""
    _user_id, user_role, mode, _thread_id, _trace_run_id = _context_from_config(config)
    payload = cli_tool_doc_payload(tool=tool, user_role=user_role, mode=mode)
    return json.dumps(payload, ensure_ascii=False)


@register_tool(
    name="dispatch_tool",
    permission="limited",
    roles=["admin", "sre", "viewer"],
    tags=["dispatcher"],
    requires_approval=False,
)
def dispatch_tool(action: str, params: Dict[str, Any], config: Optional[RunnableConfig] = None) -> str:
    """Execute one action through dispatcher policy gateway."""
    # 获取上下文
    user_id, user_role, mode, thread_id, trace_run_id = _context_from_config(config)

    # session = _get_session(user_role=user_role, mode=mode, thread_id=thread_id)

    # 获取工具调用风险
    profile = tool_approval_profile("dispatch_tool", {"action": action, "params": params})

    # 创建 tool call request 审计
    append_audit({
        "timestamp": now_iso(),
        "event": "tool_call_request",
        "tool": "dispatch_tool",
        "args": {"action": action, "params": params},
        "user_id": user_id,
        "user_role": user_role,
        "mode": mode,
        "thread_id": thread_id,
        "trace_run_id": trace_run_id,
        "tool_permission": profile.get("permission"),
        "risk_level": profile.get("risk_level"),
        "requires_approval": profile.get("requires_approval"),
    })

    # 执行 action
    payload = dispatch_action(action=action, params=params, user_role=user_role, mode=mode)

    # 记录调用后审计
    status = str(payload.get("status") or "unknown")
    event_type = "tool_call_result"
    if status == "denied":
        event_type = "tool_call_denied"
    append_audit({
        "timestamp": now_iso(),
        "event": event_type,
        "tool": "dispatch_tool",
        "args": {"action": action, "params": params},
        "user_id": user_id,
        "user_role": user_role,
        "mode": mode,
        "thread_id": thread_id,
        "trace_run_id": trace_run_id,
        "tool_permission": profile.get("permission"),
        "risk_level": profile.get("risk_level"),
        "requires_approval": profile.get("requires_approval"),
        "status": "denied" if status == "denied" else ("failed" if status == "failed" else "success"),
        "error": payload.get("error") or payload.get("reason"),
        "result": {
            "action": payload.get("action"),
            "status": status,
            "risk_level": payload.get("risk_level"),
            "requires_approval": payload.get("requires_approval"),
            "execution_backend": payload.get("execution_backend"),
            "attempts": payload.get("attempts"),
            "last_error_kind": payload.get("last_error_kind"),
        },
    })

    # session["total_dispatch"] = int(session.get("total_dispatch", 0)) + 1
    # doc_hit = action in session.get("doc_actions_viewed", set())
    # if doc_hit:
    #     session["dispatch_with_doc"] = int(session.get("dispatch_with_doc", 0)) + 1

    # payload["_meta"] = {
    #     "doc_hit": bool(doc_hit),
    #     "discovery": _discovery_meta(session, cache_hit=False),
    # }

    # logger.info(
    #     "dispatch_tool telemetry action=%s doc_hit=%s round_list=%s round_doc=%s chars=%s",
    #     action,
    #     doc_hit,
    #     session.get("round_list_calls", 0),
    #     session.get("round_doc_calls", 0),
    #     session.get("discovery_chars_this_round", 0),
    # )

    # _reset_round_state(session)

    return json.dumps(payload, ensure_ascii=False)
