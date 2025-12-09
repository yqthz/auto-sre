import json

from app.tools.security import register_tool

CMDB_DATA = {
    "payment-service": {
        "hosts": ["192.168.1.101", "192.168.1.102"],
        "container_prefix": "payment-prod",
        "type": "java"
    },
    "order-service": {
        "hosts": ["192.168.1.200"],
        "container_prefix": "order-prod",
        "type": "go"
    },
    "nginx-gateway": {
        "hosts": ["192.168.1.10"],
        "container_prefix": "nginx-ingress",
        "type": "nginx"
    }
}


@register_tool(
    name="lookup_service_info",
    permission="info",
    roles=["admin", "sre", "viewer"]
)
def lookup_service_info(service_name: str):
    """
    根据服务名称查询其部署的主机 IP (Hosts) 和容器信息。
    当用户提到模糊的服务名（如 '支付服务', 'payment'）时，先调用此工具查找具体位置。
    """
    # 简单的模糊匹配
    for key, info in CMDB_DATA.items():
        if service_name.lower() in key or key in service_name.lower():
            return json.dumps({
                "service": key,
                "deployed_hosts": info["hosts"],
                "container_pattern": info["container_prefix"],
                "tech_stack": info["type"]
            }, indent=2)

    return f"CMDB: 未找到名为 '{service_name}' 的服务。请确认服务名。"