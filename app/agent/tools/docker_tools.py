import docker

from app.agent.tools.security import register_tool, validate_shell_command

client = docker.from_env()

@register_tool(
    name="check_server_status",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"]
)
def check_server_status(container_name: str):
    """查看指定服务器(容器)的运行状态。返回 running 或 exited。"""
    if not client: return "Docker client not connected."
    try:
        container = client.containers.get(container_name)
        return f"Container {container_name} is {container.status}."
    except Exception as e:
        return f"Error: {str(e)}"

@register_tool(
    name="read_server_logs",
    permission="moderate",
    roles=["admin", "sre", "viewer"],
    param_rules=[("container_name", "prod")],
    tags=["docker"]
)
def read_server_logs(container_name: str, tail: int = 50):
    """读取服务器日志，用于分析报错原因。"""
    if not client: return "Docker client not connected."

    try:
        container = client.containers.get(container_name)
        # 获取最后几行日志
        logs = container.logs(tail=tail).decode('utf-8')
        return logs[:2000]
    except Exception as e:
        return f"Error reading logs: {str(e)}"

@register_tool(
    name="restart_server",
    permission="danger",
    roles=["admin"],
    param_rules=[("container_name", "prod")],
    tags=["docker"]
)
def restart_server(container_name: str = "target_nginx"):
    """重启服务器。只有在确定服务挂掉或需要重启时调用。"""
    if not client: return "Docker client not connected."
    try:
        container = client.containers.get(container_name)
        container.restart()
        return f"Successfully restarted {container_name}."
    except Exception as e:
        return f"Failed to restart: {str(e)}"

@register_tool(
    name="check_system_metrics",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"]
)
def check_system_metrics(container_name: str):
    """获取容器的 CPU 和 内存使用率。"""
    try:
        container = client.containers.get(container_name)
        stats = container.stats(stream=False)

        cpu_usage = stats['cpu_usage']['cpu_usage']['total_usage']
        memory_usage = stats['memory_usage']['usage']
        return f"CPU Usage: {cpu_usage}, Memory: {memory_usage} bytes"
    except Exception as e:
        return f"Error: {str(e)}"


@register_tool(
    name="exec_command_in_container",
    permission="moderate",
    roles=["admin", "sre", "viewer"],
    param_rules=[("container_name", "prod")],
    tags=["docker"]
)
def exec_command_in_container(container_name: str, command: str):
    """
    在指定容器内部执行 Shell 命令。用于查看容器内部文件或进程状态。
    例如: 'cat /var/log/nginx/error.log' 或 'ps aux'
    """
    if not client: return "Docker client not connected."

    try:
        validate_shell_command(command)
    except PermissionError as e:
        return str(e)

    try:
        container = client.containers.get(container_name)

        exit_code, output = container.exec_run(
            cmd=["/bin/sh", "-c", command],
            user="root"
        )

        decoded_output = output.decode("utf-8")

        if exit_code != 0:
            return f"Command failed (Exit {exit_code}):\n{decoded_output}"

        return decoded_output[:2000]

    except Exception as e:
        return f"Container Exec Error: {str(e)}"

@register_tool(
    name="read_file_content",
    permission="moderate",
    roles=["admin", "sre", "viewer"],
    tags=["docker"]
)
def read_file_content(target: str, file_path: str, line_limit: int = 50):
    """
    读取目标(container_name)中指定文件(file_path)的内容。
    """
    safe_cmd = f"tail -n {line_limit} {file_path}"
    return exec_command_in_container(target, safe_cmd)


@register_tool(
    name="check_process_list",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["docker"]
)
def check_process_list(target: str, grep_keyword: str = ""):
    """
    查看容器内的进程列表。可选择性过滤关键字。
    """
    cmd = "ps aux"
    if grep_keyword:
        clean_keyword = "".join(c for c in grep_keyword if c.isalnum())
        cmd += f" | grep {clean_keyword}"

    return exec_command_in_container(target, cmd)