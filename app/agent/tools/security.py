from typing import Dict, List, Optional

from app.storage import append_audit
from app.utils.format_utils import now_iso

TOOL_REGISTRY: Dict[str, dict] = {}

SENSITIVE_PATTERNS = ["prod", "payment", "database"]

SAFE_SHELL_COMMANDS = {
    "ls", "cat", "tail", "head", "grep", "find",
    "ps", "netstat", "whoami", "uptime", "free", "df", "du"
}


def validate_shell_command(command: str):
    """
    Deep security checks for shell command execution.
    1. Block composed commands (; && ||)
    2. Block output redirection (>)
    3. Enforce command verb allow-list
    """
    if any(char in command for char in [";", "&&", "||", "`", "$("]):
        raise PermissionError("Unsafe shell composition is not allowed.")

    if ">" in command:
        raise PermissionError("Output redirection is not allowed.")

    segments = command.split("|")
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue

        verb = seg.split(" ")[0]
        if verb not in SAFE_SHELL_COMMANDS:
            raise PermissionError(f"Command `{verb}` is not in safe allow-list.")

    return True


def get_sensitive_tool_names() -> List[str]:
    """Return tools that require explicit human approval."""
    sensitive = []
    for name, meta in TOOL_REGISTRY.items():
        if meta.get("requires_approval", False):
            sensitive.append(name)
    return sorted(sensitive)


def is_sensitive_tool(tool_name: str, args: Optional[dict] = None) -> bool:
    from app.agent.approval_policy import tool_approval_profile

    profile = tool_approval_profile(tool_name, args or {})
    return bool(profile.get("requires_approval", False))


def security_check(tool_name: str, args: dict, user_role: str, mode: str = "manual"):
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

    param_rules = tool_meta.get("param_rules", [])
    for key, rule in param_rules:
        val = args.get(key, "")
        if isinstance(rule, str) and rule in val:
            raise PermissionError(
                f"Parameter `{key}` value `{val}` hit sensitive keyword rule `{rule}`"
            )
        if hasattr(rule, "match") and rule.match(val):
            raise PermissionError(
                f"Parameter `{key}` value `{val}` matched sensitive pattern rule"
            )

    for v in args.values():
        if any(p in str(v) for p in SENSITIVE_PATTERNS):
            raise PermissionError(
                f"Parameter `{v}` contains sensitive keywords, tool call denied."
            )


def before_tool_execution(
    tool_name: str,
    args: dict,
    user_id: str,
    user_role: str,
    mode: str = "manual",
):
    """Pre-execution hook: authz + audit request."""
    security_check(tool_name, args, user_role, mode=mode)

    append_audit({
        "timestamp": now_iso(),
        "event": "tool_call_request",
        "tool": tool_name,
        "args": args,
        "user_id": user_id,
        "user_role": user_role,
    })


def after_tool_execution(tool_name: str, result: str, user_id: str, user_role: str):
    """Post-execution hook: audit result."""
    append_audit({
        "timestamp": now_iso(),
        "event": "tool_call_result",
        "tool": tool_name,
        "result": result[:500],
        "user_id": user_id,
        "user_role": user_role,
    })


def register_tool(
    name: str,
    permission: str = "info",
    roles=None,
    param_rules=None,
    tags=None,
    requires_approval: bool | None = None,
):
    """
    Tool registration decorator.
    :param tags: labels such as ['docker'], ['ssh'], ['network']
    """
    if roles is None:
        roles = ["admin"]
    if param_rules is None:
        param_rules = []
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
            "param_rules": param_rules,
            "requires_approval": requires_approval,
            "description": func.__doc__,
        }
        return func

    return decorator


def check_params(tool_name: str, args: dict):
    rules = TOOL_REGISTRY[tool_name].get("param_rules", [])
    for key, rule in rules:
        val = args.get(key, "")
        if isinstance(rule, str) and rule in val:
            raise PermissionError(f"Value '{val}' violates rule '{rule}'")
        if hasattr(rule, "match") and rule.match(val):
            raise PermissionError(f"Value '{val}' violates rule pattern.")
