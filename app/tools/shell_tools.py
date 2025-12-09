import shlex

from app.tools.security import register_tool
from app.tools.ssh_tools import run_ssh_cmd



@register_tool(
    name="fetch_remote_log",
    permission="moderate",
    roles=["admin", "sre"],
    param_rules=[("file_path", "/var/log")]
)
def fetch_remote_log(hostname: str, file_path: str, line: int = 50):
    """
    通过 SSH 读取远程服务器上的日志文件的最后 N 行。
    用于获取最新的报错堆栈。
    """
    if ".." in file_path:
        return "Error: Directory traversal (..) is not allowed."

    command = f"tail -n {line} {file_path}"

    try:
        content = run_ssh_cmd(hostname, command)
        if not content:
            return "Log file is empty"
        return content[:1000]
    except Exception as e:
        return f"Error reading log: {e}"

@register_tool(
    name="grep_remote_log",
    permission="moderate",
    roles=["admin", "sre"],
    param_rules=[("file_path", "/var/log")]
)
def grep_remote_log(hostname: str, file_path: str, keyword: str, context_lines: int = 2):
    """
    在远程日志文件中搜索特定关键字。
    context_lines: 匹配行的前后显示几行 (grep -C)。
    """
    if ".." in file_path: return "Error: Invalid path."

    safe_keyword = shlex.quote(keyword)

    command = f"grep -C {context_lines} {safe_keyword} {file_path} | tail -n 200"

    try:
        content = run_ssh_cmd(hostname, command)
        return content if content else f"No matches found for '{keyword}'."
    except Exception as e:
        return f"Grep failed: {e}"

@register_tool(
    name="check_server_health",
    permission="info",
    roles=["admin", "sre", "viewer"]
)
def check_server_health(hostname: str):
    """
    通过 SSH 一次性获取服务器的 CPU (top), 内存 (free), 磁盘 (df) 摘要信息。
    """
    command = "uptime && free -h && df -h / | grep /"
    try:
        return run_ssh_cmd(hostname, command)
    except Exception as e:
        return f"Health check failed: {e}"
