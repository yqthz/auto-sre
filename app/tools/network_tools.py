from app.tools.docker_tools import exec_command_in_container
from app.tools.security import register_tool


@register_tool(
    name="check_network_connectivity",
    permission="info",
    roles=["admin", "sre"],
    param_rules=[("target_host", "internal")]
)
def check_network_connectivity(source_container: str, target_host: str, port: int):
    """
    测试网络连通性 (TCP Ping/NC)。
    从 source_container 内部尝试连接 target_host:port。
    用于诊断防火墙或网络隔离问题。
    """
    cmd = f"nc -z -v -w 2 {target_host} {port}"

    return exec_command_in_container(source_container, cmd)


@register_tool(
    name="curl_http_endpoint",
    permission="moderate",
    roles=["admin", "sre"]
)
def curl_http_endpoint(source_container: str, url: str):
    """
    从容器内部发起 HTTP 请求，检查 HTTP 状态码。
    用于验证 API 是否存活。
    """
    cmd = f"curl -I -m 5 -s -o /dev/null -w '%{{http_code}}' {url}"
    return exec_command_in_container(source_container, cmd)