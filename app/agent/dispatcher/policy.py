import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.agent.dispatcher.registry import ActionMeta, get_action_meta


@dataclass(frozen=True)
class PolicyDecision:
    status: str
    reason: str
    action_meta: Optional[ActionMeta]


def _check_role(meta: ActionMeta, user_role: str) -> Optional[str]:
    if user_role not in meta.roles:
        return f"user role `{user_role}` is not allowed for action `{meta.action}`"
    return None


def _check_mode(meta: ActionMeta, mode: str) -> Optional[str]:
    if mode == "auto" and meta.requires_approval:
        return f"action `{meta.action}` is blocked in auto mode"
    return None


def _matches_type(value: Any, expected: str) -> bool:
    """类型检查"""
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True


def _validate_param_value(name: str, value: Any, rule: Dict[str, Any]) -> Optional[str]:
    """参数校验"""
    # 类型检查
    expected_type = str(rule.get("type") or "")
    if expected_type and not _matches_type(value, expected_type):
        return f"param `{name}` type mismatch: expected {expected_type}, got {type(value).__name__}"

    # enum 检查
    enum_values = rule.get("enum")
    if isinstance(enum_values, list) and enum_values and value not in enum_values:
        return f"param `{name}` must be one of {enum_values}"

    # 参数范围检查
    if expected_type in {"integer", "number"}:
        minimum = rule.get("minimum")
        maximum = rule.get("maximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            return f"param `{name}` must be >= {minimum}"
        if isinstance(maximum, (int, float)) and value > maximum:
            return f"param `{name}` must be <= {maximum}"

    # 字符串检查
    if expected_type == "string":
        min_len = rule.get("minLength")
        max_len = rule.get("maxLength")
        pattern = rule.get("pattern")
        if isinstance(min_len, int) and len(value) < min_len:
            return f"param `{name}` length must be >= {min_len}"
        if isinstance(max_len, int) and len(value) > max_len:
            return f"param `{name}` length must be <= {max_len}"
        if isinstance(pattern, str) and pattern and not re.search(pattern, value):
            return f"param `{name}` does not match required pattern"

    return None


def validate_params_with_schema(params: Dict[str, Any], schema: Dict[str, Any]) -> Optional[str]:
    if not isinstance(params, dict):
        return "params must be a json object"

    required = schema.get("required") or []
    missing = [k for k in required if k not in params]
    if missing:
        return f"missing required params: {missing}"

    properties = schema.get("properties") or {}
    allow_extra = bool(schema.get("additionalProperties", False))

    if not allow_extra:
        unknown = [k for k in params.keys() if k not in properties]
        if unknown:
            return f"unknown params are not allowed: {unknown}"

    for name, value in params.items():
        rule = properties.get(name)
        if not isinstance(rule, dict):
            continue

        err = _validate_param_value(name=name, value=value, rule=rule)
        if err:
            return err

    return None


def _check_schema(meta: ActionMeta, params: Dict[str, Any]) -> Optional[str]:
    schema_error = validate_params_with_schema(params=params, schema=meta.param_schema)
    if schema_error:
        return f"schema validation failed: {schema_error}"
    return None


def evaluate_action(action: str, params: Dict[str, Any], user_role: str, mode: str) -> PolicyDecision:
    """评估执行的工具"""
    # 获取工具元数据
    meta = get_action_meta(action)
    if not meta:
        return PolicyDecision(status="denied", reason=f"unknown action `{action}`", action_meta=None)

    # 检查参数
    schema_error = _check_schema(meta, params)
    if schema_error:
        return PolicyDecision(status="denied", reason=schema_error, action_meta=meta)

    # 校验角色
    role_error = _check_role(meta, user_role)
    if role_error:
        return PolicyDecision(status="denied", reason=role_error, action_meta=meta)

    # 检查运行模式
    mode_error = _check_mode(meta, mode)
    if mode_error:
        return PolicyDecision(status="denied", reason=mode_error, action_meta=meta)
    return PolicyDecision(status="allowed", reason="", action_meta=meta)
