from typing import Dict

TOOL_REGISTRY: Dict[str, dict] = {}


def security_check(tool_name: str, user_role: str, mode: str = "manual"):
    """Security validation for tool call."""

    if tool_name not in TOOL_REGISTRY:
        raise PermissionError(f"Unknown tool `{tool_name}`!")

    tool_meta = TOOL_REGISTRY[tool_name]

    # Auto mode is diagnostics-only and must never execute sensitive tools.
    if mode == "auto" and tool_meta.get("requires_approval", False):
        raise PermissionError(
            f"Tool `{tool_name}` is blocked in auto mode (diagnostics-only)."
        )

    if user_role not in tool_meta["roles"]:
        raise PermissionError(
            f"User role `{user_role}` is not allowed to call `{tool_name}`!"
        )


def before_tool_execution(
    tool_name: str,
    args: dict,
    user_id: str,
    user_role: str,
    mode: str = "manual",
):
    """Pre-execution hook: authz only."""
    security_check(tool_name, user_role, mode=mode)


def after_tool_execution(tool_name: str, result: str, user_id: str, user_role: str):
    """Post-execution hook kept for compatibility; no audit writes."""
    _ = (tool_name, result, user_id, user_role)


def register_tool(
    name: str,
    permission: str = "info",
    roles=None,
    tags=None,
    requires_approval: bool | None = None,
):
    """
    Register a callable as an agent tool and store its metadata in ``TOOL_REGISTRY``.

    Args:
        name: Unique tool name used by the tool router.
        permission: Coarse permission/risk label used by policy and audit. Common values:
            - ``info``: low-risk read/diagnostic operations
            - ``limited`` / ``moderate``: medium-risk operations
            - ``danger``: high-risk operations
        roles: Allowed caller roles (for example ``["admin", "sre"]``). Defaults to
            ``["admin"]`` when omitted.
        tags: Optional labels for grouping or filtering tools (for example
            ``["docker"]``, ``["network"]``).
        requires_approval: Whether the tool is considered approval-gated/sensitive.
            - ``None`` (default): auto-derived from ``permission == "danger"``
            - explicit ``True``/``False``: overrides the default derivation
    """
    if roles is None:
        roles = ["admin"]
    if tags is None:
        tags = []
    if requires_approval is None:
        requires_approval = permission == "danger"

    def decorator(func):
        TOOL_REGISTRY[name] = {
            "fn": func,
            "name": name,
            "permission": permission,
            "roles": roles,
            "tags": tags,
            "requires_approval": requires_approval,
            "description": func.__doc__,
        }
        return func

    return decorator
