import json
import time
from typing import Any, Dict, Optional

from app.agent.dispatcher.discovery import cli_list_payload, cli_tool_doc_payload
from app.agent.dispatcher.executor import dispatch_action
from app.agent.tools.security import register_tool
from app.core.logger import logger
from langchain_core.runnables import RunnableConfig

_DISCOVERY_TTL_SECONDS = 600
_MAX_LIST_CALLS_PER_ROUND = 1
_MAX_DOC_CALLS_PER_ROUND = 2

_DISCOVERY_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _context_from_config(config: Optional[RunnableConfig]) -> tuple[str, str, str]:
    cfg = dict(config or {})
    configurable = dict(cfg.get("configurable") or {})
    user_role = str(configurable.get("user_role") or "viewer")
    mode = str(configurable.get("mode") or "manual")
    thread_id = str(configurable.get("thread_id") or "global")
    return user_role, mode, thread_id


def _session_key(user_role: str, mode: str, thread_id: str) -> str:
    return f"{thread_id}:{user_role}:{mode}"


def _new_session(now_ts: float) -> Dict[str, Any]:
    return {
        "expires_at": now_ts + _DISCOVERY_TTL_SECONDS,
        "list_cache": None,
        "doc_cache": {},
        "round_list_calls": 0,
        "round_doc_calls": 0,
        "doc_actions_viewed": set(),
        "discovery_chars_this_round": 0,
        "total_dispatch": 0,
        "dispatch_with_doc": 0,
    }


def _get_session(user_role: str, mode: str, thread_id: str) -> Dict[str, Any]:
    now_ts = time.time()
    key = _session_key(user_role=user_role, mode=mode, thread_id=thread_id)
    current = _DISCOVERY_SESSIONS.get(key)
    if current is None or current.get("expires_at", 0) <= now_ts:
        current = _new_session(now_ts)
        _DISCOVERY_SESSIONS[key] = current
    return current


def _json_size(payload: Dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False))


def _discovery_budget_error(kind: str) -> Dict[str, Any]:
    return {
        "error": "discovery_budget_exceeded",
        "detail": f"{kind} budget exceeded in current round",
        "allowed": {
            "cli_list": _MAX_LIST_CALLS_PER_ROUND,
            "cli_tool_doc": _MAX_DOC_CALLS_PER_ROUND,
        },
    }


def _discovery_meta(session: Dict[str, Any], cache_hit: bool) -> Dict[str, Any]:
    total_dispatch = int(session.get("total_dispatch", 0))
    dispatch_with_doc = int(session.get("dispatch_with_doc", 0))
    hit_rate = (dispatch_with_doc / total_dispatch) if total_dispatch > 0 else 0.0

    return {
        "cache_hit": cache_hit,
        "ttl_seconds": _DISCOVERY_TTL_SECONDS,
        "round_budget": {
            "cli_list": _MAX_LIST_CALLS_PER_ROUND,
            "cli_tool_doc": _MAX_DOC_CALLS_PER_ROUND,
        },
        "round_usage": {
            "cli_list": int(session.get("round_list_calls", 0)),
            "cli_tool_doc": int(session.get("round_doc_calls", 0)),
        },
        "discovery_chars_this_round": int(session.get("discovery_chars_this_round", 0)),
        "doc_hit_rate": round(hit_rate, 4),
    }


def _reset_round_state(session: Dict[str, Any]) -> None:
    session["round_list_calls"] = 0
    session["round_doc_calls"] = 0
    session["doc_actions_viewed"] = set()
    session["discovery_chars_this_round"] = 0


@register_tool(
    name="cli_list",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["dispatcher"],
    requires_approval=False,
)
def cli_list(config: Optional[RunnableConfig] = None) -> str:
    """List available tool groups and actions for current session."""
    user_role, mode, thread_id = _context_from_config(config)
    session = _get_session(user_role=user_role, mode=mode, thread_id=thread_id)

    cached = session.get("list_cache")
    if isinstance(cached, dict):
        payload = dict(cached)
        payload["_meta"] = _discovery_meta(session, cache_hit=True)
        return json.dumps(payload, ensure_ascii=False)

    if int(session.get("round_list_calls", 0)) >= _MAX_LIST_CALLS_PER_ROUND:
        return json.dumps(_discovery_budget_error("cli_list"), ensure_ascii=False)

    payload = cli_list_payload(user_role=user_role, mode=mode)
    session["list_cache"] = dict(payload)
    session["round_list_calls"] = int(session.get("round_list_calls", 0)) + 1
    session["discovery_chars_this_round"] = int(session.get("discovery_chars_this_round", 0)) + _json_size(payload)

    payload["_meta"] = _discovery_meta(session, cache_hit=False)
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
    user_role, mode, thread_id = _context_from_config(config)
    session = _get_session(user_role=user_role, mode=mode, thread_id=thread_id)

    doc_cache = session.setdefault("doc_cache", {})
    cached = doc_cache.get(tool)
    if isinstance(cached, dict):
        payload = dict(cached)
        payload["_meta"] = _discovery_meta(session, cache_hit=True)
        return json.dumps(payload, ensure_ascii=False)

    if int(session.get("round_doc_calls", 0)) >= _MAX_DOC_CALLS_PER_ROUND:
        return json.dumps(_discovery_budget_error("cli_tool_doc"), ensure_ascii=False)

    payload = cli_tool_doc_payload(tool=tool, user_role=user_role, mode=mode)
    doc_cache[tool] = dict(payload)
    session["round_doc_calls"] = int(session.get("round_doc_calls", 0)) + 1
    session["discovery_chars_this_round"] = int(session.get("discovery_chars_this_round", 0)) + _json_size(payload)

    for item in payload.get("actions", []):
        action = item.get("action")
        if isinstance(action, str) and action:
            session["doc_actions_viewed"].add(action)

    payload["_meta"] = _discovery_meta(session, cache_hit=False)
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
    user_role, mode, thread_id = _context_from_config(config)
    session = _get_session(user_role=user_role, mode=mode, thread_id=thread_id)

    payload = dispatch_action(action=action, params=params, user_role=user_role, mode=mode)

    session["total_dispatch"] = int(session.get("total_dispatch", 0)) + 1
    doc_hit = action in session.get("doc_actions_viewed", set())
    if doc_hit:
        session["dispatch_with_doc"] = int(session.get("dispatch_with_doc", 0)) + 1

    payload["_meta"] = {
        "doc_hit": bool(doc_hit),
        "discovery": _discovery_meta(session, cache_hit=False),
    }

    logger.info(
        "dispatch_tool telemetry action=%s doc_hit=%s round_list=%s round_doc=%s chars=%s",
        action,
        doc_hit,
        session.get("round_list_calls", 0),
        session.get("round_doc_calls", 0),
        session.get("discovery_chars_this_round", 0),
    )

    _reset_round_state(session)

    return json.dumps(payload, ensure_ascii=False)
