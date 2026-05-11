import json
from typing import Any, Dict, Optional

from app.agent.dispatcher.discovery import cli_action_doc_payload, cli_list_payload
from app.agent.dispatcher.executor import dispatch_action
from app.agent.tools.security import register_tool
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
    """
    列出当前会话可用的工具簇与 action。

    功能解释:
    - 返回 agent 当前可见的工具目录。
    - 结果按 tool group 分组，每个 action 包含名称、描述、风险等级和审批标记。

    使用场景:
    - 让 agent 先发现可用工具，再决定下一步调用哪个 action。
    - 做工具能力枚举和权限过滤结果检查。

    参数说明:
    - `config` (RunnableConfig，可选)：运行上下文，通常由框架注入。

    必填字段:
    - 无。

    调用方法:
    - `cli_list()`

    返回关键字段:
    - `tools`：工具簇数组。
    - 每个工具簇包含 `tool` 与 `actions`。
    - 每个 action 包含 `name`、`description`、`risk_level`、`requires_approval`。
    """
    _user_id, user_role, mode, _thread_id, _trace_run_id = _context_from_config(config)
    payload = cli_list_payload(user_role=user_role, mode=mode)
    return json.dumps(payload, ensure_ascii=False)


@register_tool(
    name="cli_action_doc",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["dispatcher"],
    requires_approval=False,
    description="Get doc string for one action",
)
def cli_action_doc(action: str, config: Optional[RunnableConfig] = None) -> str:
    """
    获取某个 action 的 doc string。

    功能解释:
    - 返回指定 action 的详细使用文档。
    - 适合在参数不确定、调用方式不明确时做精确查阅。

    使用场景:
    - agent 已知道 action 名称，但还不知道具体字段含义、默认值、约束和调用示例。
    - 需要补充某个 action 的详细说明时。

    参数说明:
    - `action` (str，必填)：完整 action 名称。
    - `config` (RunnableConfig，可选)：运行上下文。

    必填字段:
    - `action`

    调用方法:
    - `cli_action_doc(action="prometheus.query_prometheus_metrics")`

    返回关键字段:
    - `action`：入参回显。
    - `doc`：该 action 的 doc string。
    - 找不到或无权限时 `doc` 为空字符串。
    """
    _user_id, user_role, mode, _thread_id, _trace_run_id = _context_from_config(config)
    payload = cli_action_doc_payload(action=action, user_role=user_role, mode=mode)
    return json.dumps(payload, ensure_ascii=False)


# @register_tool(
#     name="cli_tool_doc",
#     permission="info",
#     roles=["admin", "sre", "viewer"],
#     tags=["dispatcher"],
#     requires_approval=False,
#     description="Get structured docs for one tool group (legacy)",
# )
# def cli_tool_doc(tool: str, config: Optional[RunnableConfig] = None) -> str:
#     """Get minimal structured doc for a tool group."""
#     _user_id, user_role, mode, _thread_id, _trace_run_id = _context_from_config(config)
#     payload = cli_tool_doc_payload(tool=tool, user_role=user_role, mode=mode)
#     return json.dumps(payload, ensure_ascii=False)


@register_tool(
    name="dispatch_tool",
    permission="limited",
    roles=["admin", "sre", "viewer"],
    tags=["dispatcher"],
    requires_approval=False,
)
def dispatch_tool(action: str, params: Dict[str, Any], config: Optional[RunnableConfig] = None) -> str:
    """
    通过分发器执行指定 action。

    功能解释:
    - 按当前角色和模式执行权限校验后调用具体工具。
    - 是真正的工具执行入口，而不是发现接口。

    使用场景:
    - agent 已决定要调用哪个 action，并准备好参数时。
    - 统一走策略网关执行动作。

    参数说明:
    - `action` (str，必填)：完整 action 名称。
    - `params` (dict，必填)：传给目标工具的参数字典。
    - `config` (RunnableConfig，可选)：运行上下文。

    必填字段:
    - `action`
    - `params`

    调用方法:
    - `dispatch_tool(action="check_actuator_health", params={})`
    - `dispatch_tool(action="query_prometheus_by_promql", params={"promql":"up"})`

    返回关键字段:
    - 返回目标 action 的执行结果字符串，通常是 JSON。
    - 若权限或参数错误，返回错误结构。
    """
    # 保留上下文提取，便于未来扩展，不在执行层写审计日志。
    _user_id, user_role, mode, _thread_id, _trace_run_id = _context_from_config(config)

    # 执行 action
    payload = dispatch_action(action=action, params=params, user_role=user_role, mode=mode)

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
