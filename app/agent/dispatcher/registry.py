import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Union, get_args, get_origin

from app.agent.tools.loader import ensure_tool_modules_loaded
from app.agent.tools.security import TOOL_REGISTRY

ALERT_NAME_ENUM = [
    "HighMemoryUsage",
    "HighErrorRate",
    "HighCPUUsage",
    "HighDatabaseConnections",
    "InstanceDown",
    "LongGC",
    "HighThreadCount",
    "HighResponseTime",
]

ACTION_SCHEMA_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "log.overview_log_issues": {
        "properties": {
            "alert_time": {
                "type": "string",
                "minLength": 1,
                "pattern": r"^\d{4}-\d{2}-\d{2}T",
            },
            "window_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 60,
            },
        },
        "additionalProperties": False,
    },
    "log.analyze_log_around_alert": {
        "properties": {
            "alert_time": {
                "type": "string",
                "minLength": 1,
                "pattern": r"^\d{4}-\d{2}-\d{2}T",
            },
            "window_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 60,
            },
        },
        "additionalProperties": False,
    },
    "log.analyze_slow_requests": {
        "properties": {
            "alert_time": {
                "type": "string",
                "minLength": 1,
                "pattern": r"^\d{4}-\d{2}-\d{2}T",
            },
            "window_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 60,
            },
            "min_cost_ms": {
                "type": "integer",
                "minimum": 1,
                "maximum": 600000,
            },
            "max_requests": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
            },
        },
        "additionalProperties": False,
    },
    "log.analyze_error_requests": {
        "properties": {
            "alert_time": {
                "type": "string",
                "minLength": 1,
                "pattern": r"^\d{4}-\d{2}-\d{2}T",
            },
            "window_minutes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 60,
            },
            "min_status": {
                "type": "integer",
                "minimum": 400,
                "maximum": 599,
            },
            "max_requests": {
                "type": "integer",
                "minimum": 1,
                "maximum": 100,
            },
        },
        "additionalProperties": False,
    },
    "prometheus.query_prometheus_metrics": {
        "properties": {
            "alert_name": {
                "type": "string",
                "enum": ALERT_NAME_ENUM,
            },
            "instance": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    },
}

DEFAULT_ACTION_TIMEOUT_SECONDS = 10         # 超时时间
DEFAULT_ACTION_MAX_RETRIES = 1              # 最大重试次数
DEFAULT_ACTION_RETRY_BACKOFF_SECONDS = 0.5          # 退避策略
DEFAULT_ACTION_RETRY_BACKOFF_MULTIPLIER = 2.0
DEFAULT_ACTION_RETRY_ON_KINDS = ["timeout", "spawn_error", "cli_failed"]     # 重试类型
MAX_ACTION_TIMEOUT_SECONDS = 120

ACTION_RUNTIME_OVERRIDES: Dict[str, Dict[str, Any]] = {}


@dataclass(frozen=True)
class ActionMeta:
    action: str
    tool_name: str
    tool_group: str
    fn: Callable[..., Any]
    description: str
    doc: str
    roles: List[str]      
    permission: str
    requires_approval: bool
    risk_level: str
    required_params: List[str]
    param_types: Dict[str, str]
    param_schema: Dict[str, Any]
    timeout_seconds: int
    max_retries: int
    retry_backoff_seconds: float
    retry_backoff_multiplier: float
    retry_on_kinds: List[str]


def _permission_to_risk(permission: str) -> str:
    """permission 转为 risk"""
    if permission == "danger":
        return "high"
    if permission in {"limited", "moderate"}:
        return "medium"
    return "low"


def _annotation_to_json_type(annotation: Any) -> str:
    """类型注解 转成 json schema 类型"""
    if annotation is inspect._empty:
        return "string"

    origin = get_origin(annotation)
    if origin is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _annotation_to_json_type(args[0])

    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    if annotation is float:
        return "number"
    if annotation is str:
        return "string"
    if annotation in {list, tuple, set}:
        return "array"
    if annotation is dict:
        return "object"

    return "string" 


def _json_type_to_legacy(json_type: str) -> str:
    """类型映射"""
    mapping = {
        "integer": "int",
        "number": "float",
        "boolean": "bool",
        "string": "string",
        "array": "string",
        "object": "string",
    }
    return mapping.get(json_type, "string")


def _infer_param_schema(fn: Callable[..., Any]) -> tuple[List[str], Dict[str, str], Dict[str, Any]]:
    # 读取函数参数列表
    sig = inspect.signature(fn)
    required: List[str] = []
    param_types: Dict[str, str] = {}
    properties: Dict[str, Dict[str, Any]] = {}

    # 遍历参数，保留可按关键字传参的类型
    for name, param in sig.parameters.items():
        if param.kind not in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        }:
            continue

        if name == "config":
            continue

        # 参数没有默认值，标记为 required
        if param.default is inspect._empty:
            required.append(name)

        # 参数注解转成 JSON 类型
        json_type = _annotation_to_json_type(param.annotation)

        param_types[name] = _json_type_to_legacy(json_type)
        properties[name] = {"type": json_type}

    schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }

    return required, param_types, schema


def _tool_group_from_meta(meta: dict) -> str:
    """获取工具 tags"""
    tags = meta.get("tags") or []
    if tags:
        return str(tags[0])
    return "misc"


def _deep_merge_schema(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)

    base_props = dict(base.get("properties") or {})
    override_props = dict(override.get("properties") or {})
    if override_props:
        for key, value in override_props.items():
            next_prop = dict(base_props.get(key) or {})
            next_prop.update(value)
            base_props[key] = next_prop
        merged["properties"] = base_props

    for key, value in override.items():
        if key == "properties":
            continue
        merged[key] = value

    required = merged.get("required") or []
    if isinstance(required, list):
        dedup: List[str] = []
        seen = set()
        for item in required:
            if item in seen:
                continue
            seen.add(item)
            dedup.append(item)
        merged["required"] = dedup

    return merged


def _normalize_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(schema)
    props = normalized.get("properties") or {}

    normalized_props: Dict[str, Dict[str, Any]] = {}
    for key, value in props.items():
        prop = dict(value or {})
        ptype = prop.get("type")
        if isinstance(ptype, str):
            prop["type"] = ptype.lower()

        pattern = prop.get("pattern")
        if isinstance(pattern, str):
            # Pre-check invalid regex and surface at startup.
            re.compile(pattern)

        normalized_props[key] = prop

    normalized["properties"] = normalized_props
    normalized["required"] = list(normalized.get("required") or [])
    if "additionalProperties" not in normalized:
        normalized["additionalProperties"] = False

    return normalized


def _normalize_runtime_config(raw: Dict[str, Any], *, requires_approval: bool, risk_level: str) -> Dict[str, Any]:
    timeout_seconds = int(raw.get("timeout_seconds", DEFAULT_ACTION_TIMEOUT_SECONDS))
    if timeout_seconds <= 0:
        timeout_seconds = DEFAULT_ACTION_TIMEOUT_SECONDS
    timeout_seconds = min(timeout_seconds, MAX_ACTION_TIMEOUT_SECONDS)

    default_retries = 0 if (requires_approval or risk_level == "high") else DEFAULT_ACTION_MAX_RETRIES
    max_retries = int(raw.get("max_retries", default_retries))
    if max_retries < 0:
        max_retries = 0

    retry_backoff_seconds = float(raw.get("retry_backoff_seconds", DEFAULT_ACTION_RETRY_BACKOFF_SECONDS))
    if retry_backoff_seconds < 0:
        retry_backoff_seconds = 0.0

    retry_backoff_multiplier = float(raw.get("retry_backoff_multiplier", DEFAULT_ACTION_RETRY_BACKOFF_MULTIPLIER))
    if retry_backoff_multiplier < 1:
        retry_backoff_multiplier = 1.0

    retry_on_kinds_raw = raw.get("retry_on_kinds", DEFAULT_ACTION_RETRY_ON_KINDS)
    if not isinstance(retry_on_kinds_raw, list):
        retry_on_kinds_raw = DEFAULT_ACTION_RETRY_ON_KINDS
    retry_on_kinds = [str(item) for item in retry_on_kinds_raw if str(item).strip()]
    if not retry_on_kinds:
        retry_on_kinds = list(DEFAULT_ACTION_RETRY_ON_KINDS)

    return {
        "timeout_seconds": timeout_seconds,
        "max_retries": max_retries,
        "retry_backoff_seconds": retry_backoff_seconds,
        "retry_backoff_multiplier": retry_backoff_multiplier,
        "retry_on_kinds": retry_on_kinds,
    }


def list_actions() -> List[ActionMeta]:
    """返回所有的工具"""
    ensure_tool_modules_loaded()
    actions: List[ActionMeta] = []

    for tool_name, meta in TOOL_REGISTRY.items():
        # Meta-tools are exposed directly and should not be re-dispatched.
        if tool_name in {"cli_list", "cli_action_doc", "dispatch_tool"}:
            continue

        fn = meta["fn"]
        group = _tool_group_from_meta(meta)
        action = f"{group}.{tool_name}"
        # 推导参数 schema
        required_params, param_types, base_schema = _infer_param_schema(fn)
        override = ACTION_SCHEMA_OVERRIDES.get(action) or {}
        param_schema = _normalize_schema(_deep_merge_schema(base_schema, override))
        # 获取 risk_level 和 审批请求
        risk_level = _permission_to_risk(str(meta.get("permission") or "info"))
        requires_approval = bool(meta.get("requires_approval", False))
        # 生成运行超时/重试配置
        runtime_config = _normalize_runtime_config(
            ACTION_RUNTIME_OVERRIDES.get(action) or {},
            requires_approval=requires_approval,
            risk_level=risk_level,
        )

        actions.append(
            ActionMeta(
                action=action,
                tool_name=tool_name,
                tool_group=group,
                fn=fn,
                description=(meta.get("description") or "").strip(),
                doc=(meta.get("doc") or "").strip(),
                roles=list(meta.get("roles") or []),
                permission=str(meta.get("permission") or "info"),
                requires_approval=requires_approval,
                risk_level=risk_level,
                required_params=required_params,
                param_types=param_types,
                param_schema=param_schema,
                timeout_seconds=runtime_config["timeout_seconds"],
                max_retries=runtime_config["max_retries"],
                retry_backoff_seconds=runtime_config["retry_backoff_seconds"],
                retry_backoff_multiplier=runtime_config["retry_backoff_multiplier"],
                retry_on_kinds=runtime_config["retry_on_kinds"],
            )
        )

    return sorted(actions, key=lambda x: x.action)


def get_action_meta(action: str) -> Optional[ActionMeta]:
    for item in list_actions():
        if item.action == action:
            return item
    return None


def group_actions_by_tool(actions: List[ActionMeta]) -> Dict[str, List[ActionMeta]]:
    grouped: Dict[str, List[ActionMeta]] = {}
    for item in actions:
        grouped.setdefault(item.tool_group, []).append(item)
    return grouped
