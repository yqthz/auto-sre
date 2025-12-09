from app.storage import append_audit
from app.utils.format_utils import now_iso

TOOL_REGISTRY = {}

SENSITIVE_PATTERNS = ["prod", "payment", "database"]

SAFE_SHELL_COMMANDS = {
    "ls", "cat", "tail", "head", "grep", "find",
    "ps", "netstat", "whoami", "uptime", "free", "df", "du"
}

def validate_shell_command(command: str):
    """
       深度检查 Shell 命令安全性
       1. 禁止多条命令拼接 (; && ||)
       2. 禁止重定向 (>)
       3. 检查命令动词是否在白名单
       """
    if any(char in command for char in [";", "&&", "||", "`", "$("]):
        raise PermissionError("🚫 禁止执行复合命令或子 shell，请单条执行。")

    if ">" in command:
        raise PermissionError("🚫 禁止文件重定向写入操作。")

    # 将命令按管道符拆分，分别检查每一段的动词
    segments = command.split("|")
    for seg in segments:
        seg = seg.strip()
        if not seg: continue

        verb = seg.split(" ")[0]
        if verb not in SAFE_SHELL_COMMANDS:
            raise PermissionError(f"🚫 命令 `{verb}` 不在安全白名单中！")

    return True


def security_check(tool_name: str, args: dict, user_role: str):
    """安全检查逻辑"""

    if tool_name not in TOOL_REGISTRY:
        raise PermissionError(f"Unknown tool `{tool_name}`!")

    tool_meta = TOOL_REGISTRY[tool_name]
    if user_role not in tool_meta["roles"]:
        raise PermissionError(
            f"User role `{user_role}` is not allowed to call `{tool_name}`!"
        )

    param_rules = tool_meta.get("param_rules", [])
    for key, rule in param_rules:
        val = args.get(key, "")
        if isinstance(rule, str) and rule in val:
            raise PermissionError(
                f"🚫 参数 `{key}` 值 `{val}` 命中敏感关键字 `{rule}`"
            )
        if hasattr(rule, "match") and rule.match(val):
            raise PermissionError(
                f"🚫 参数 `{key}` 值 `{val}` 匹配到了敏感模式规则"
            )

    # 参数敏感关键词检测
    for v in args.values():
        if any(p in str(v) for p in SENSITIVE_PATTERNS):
            raise PermissionError(f"🚫 参数 `{v}` 包含敏感关键字，禁止调用。")


def before_tool_execution(tool_name: str, args: dict, user_id: str, user_role: str):
    """执行前调用：权限验证 + 审计记录（预执行）"""
    security_check(tool_name, args, user_role)

    append_audit({
        "timestamp": now_iso(),
        "event": "tool_call_request",
        "tool": tool_name,
        "args": args,
        "user_id": user_id,
        "user_role": user_role
    })


def after_tool_execution(tool_name: str, result: str, user_id: str, user_role: str):
    """执行后审计结果"""
    append_audit({
        "timestamp": now_iso(),
        "event": "tool_call_result",
        "tool": tool_name,
        "result": result[:500],
        "user_id": user_id,
        "user_role": user_role
    })

def register_tool(name: str, permission: str = "info", roles=None, param_rules=None, tags=None):
    """
    注册工具装饰器
    :param tags: 给工具打标签，如 ['docker'], ['ssh'], ['network']
    """
    if roles is None:
        roles = ["admin"]
    if param_rules is None:
        param_rules = []
    if tags is None:
        tags = []

    def decorator(func):
        TOOL_REGISTRY[name] = {
            "fn": func,
            "name": name,
            "permission": permission,
            "roles": roles,
            "tags": tags,
            "param_rules": param_rules,
            "description": func.__doc__,
        }
        return func
    return decorator

def check_params(tool_name: str, args: dict):
    rules = TOOL_REGISTRY[tool_name].get("param_rules", [])
    for key, r in rules:
        val = args.get(key, "")
        if isinstance(r, str) and r in val:
            raise PermissionError(f"Value '{val}' violates rule '{r}'")
        if hasattr(r, "match") and r.match(val):
            raise PermissionError(f"Value '{val}' violates rule pattern.")
