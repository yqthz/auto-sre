import json
import os
from typing import Dict, List, Tuple


DEFAULT_APPROVAL_TTL_SECONDS = 300

# risk_level -> allowed approver roles
DEFAULT_APPROVAL_POLICY: Dict[str, List[str]] = {
    "low": ["viewer", "sre", "admin"],
    "medium": ["sre", "admin"],
    "high": ["admin"],
}
DEFAULT_TOOL_APPROVAL_POLICY: Dict[str, Dict[str, List[str]]] = {}


def _load_approval_ttl_seconds() -> int:
    raw = os.getenv("AGENT_APPROVAL_TTL_SECONDS")
    if not raw:
        return DEFAULT_APPROVAL_TTL_SECONDS
    try:
        ttl = int(raw)
        return ttl if ttl > 0 else DEFAULT_APPROVAL_TTL_SECONDS
    except ValueError:
        return DEFAULT_APPROVAL_TTL_SECONDS


def _normalize_policy(policy: Dict[str, List[str]]) -> Dict[str, List[str]]:
    normalized: Dict[str, List[str]] = {}
    for risk, roles in policy.items():
        risk_key = str(risk).strip().lower()
        if not risk_key:
            continue
        role_list = [str(role).strip().lower() for role in roles if str(role).strip()]
        if role_list:
            normalized[risk_key] = role_list
    return normalized


def _normalize_tool_policy(
    tool_policy: Dict[str, Dict[str, List[str]]],
) -> Dict[str, Dict[str, List[str]]]:
    normalized: Dict[str, Dict[str, List[str]]] = {}
    for tool_name, per_risk in tool_policy.items():
        tool_key = str(tool_name).strip().lower()
        if not tool_key or not isinstance(per_risk, dict):
            continue
        normalized_per_risk = _normalize_policy(per_risk)
        if normalized_per_risk:
            normalized[tool_key] = normalized_per_risk
    return normalized


def _load_approval_policy() -> Dict[str, List[str]]:
    raw = os.getenv("AGENT_APPROVAL_POLICY_JSON")
    if not raw:
        return DEFAULT_APPROVAL_POLICY

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_APPROVAL_POLICY

    if not isinstance(parsed, dict):
        return DEFAULT_APPROVAL_POLICY

    typed: Dict[str, List[str]] = {}
    for risk, roles in parsed.items():
        if isinstance(roles, list):
            typed[str(risk)] = [str(role) for role in roles]

    normalized = _normalize_policy(typed)
    return normalized or DEFAULT_APPROVAL_POLICY


def _load_tool_approval_policy() -> Dict[str, Dict[str, List[str]]]:
    raw = os.getenv("AGENT_TOOL_APPROVAL_POLICY_JSON")
    if not raw:
        return DEFAULT_TOOL_APPROVAL_POLICY

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return DEFAULT_TOOL_APPROVAL_POLICY

    if not isinstance(parsed, dict):
        return DEFAULT_TOOL_APPROVAL_POLICY

    typed: Dict[str, Dict[str, List[str]]] = {}
    for tool_name, per_risk in parsed.items():
        if not isinstance(per_risk, dict):
            continue
        typed_per_risk: Dict[str, List[str]] = {}
        for risk_level, roles in per_risk.items():
            if isinstance(roles, list):
                typed_per_risk[str(risk_level)] = [str(role) for role in roles]
        if typed_per_risk:
            typed[str(tool_name)] = typed_per_risk

    normalized = _normalize_tool_policy(typed)
    return normalized or DEFAULT_TOOL_APPROVAL_POLICY


APPROVAL_TTL_SECONDS = _load_approval_ttl_seconds()
APPROVAL_POLICY = _load_approval_policy()
TOOL_APPROVAL_POLICY = _load_tool_approval_policy()


def allowed_roles_for_risk(risk_level: str) -> List[str]:
    return APPROVAL_POLICY.get(risk_level.lower(), ["admin"])


def allowed_roles_for_tool_and_risk(tool_name: str, risk_level: str) -> List[str]:
    normalized_risk = risk_level.lower()
    normalized_tool = tool_name.strip().lower()
    if normalized_tool:
        tool_policy = TOOL_APPROVAL_POLICY.get(normalized_tool, {})
        if normalized_risk in tool_policy:
            return tool_policy[normalized_risk]
    wildcard_policy = TOOL_APPROVAL_POLICY.get("*", {})
    if normalized_risk in wildcard_policy:
        return wildcard_policy[normalized_risk]
    return allowed_roles_for_risk(normalized_risk)


def check_approval_permission(
    risk_level: str,
    approver_role: str,
    tool_name: str | None = None,
) -> Tuple[bool, str]:
    roles = (
        allowed_roles_for_tool_and_risk(tool_name, risk_level)
        if tool_name
        else allowed_roles_for_risk(risk_level)
    )
    normalized_role = approver_role.lower()
    if normalized_role in roles:
        return True, ""
    tool_hint = f" for tool `{tool_name}`" if tool_name else ""
    return False, (
        f"Role `{approver_role}` is not allowed to approve risk `{risk_level}`{tool_hint}. "
        f"Allowed roles: {roles}"
    )
