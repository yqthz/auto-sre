import json

from app.agent.tools.security import register_tool

CMDB_DATA = {
    "payment-service": {
        "app_container": "payment-prod",
        "db_container": "payment-db",
    },
    "order-service": {
        "app_container": "order-prod",
        "db_container": "order-db",
    },
    "nginx-gateway": {
        "app_container": "nginx-ingress",
        "db_container": "",
    },
}


@register_tool(
    name="lookup_service_info",
    permission="info",
    roles=["admin", "sre", "viewer"]
)
def lookup_service_info(service_name: str):
    """
    根据服务名称查询服务对应容器信息（极简定位版）。
    """
    query = (service_name or "").strip().lower()
    if not query:
        return json.dumps(
            {
                "ok": False,
                "error_code": "INVALID_ARGUMENT",
                "message": "service_name must not be empty",
            },
            ensure_ascii=False,
        )

    matches = []
    for key, info in CMDB_DATA.items():
        key_lower = key.lower()
        if query in key_lower or key_lower in query:
            matches.append((key, info))

    if not matches:
        return json.dumps(
            {
                "ok": False,
                "error_code": "SERVICE_NOT_FOUND",
                "message": f"未找到名为 '{service_name}' 的服务",
            },
            ensure_ascii=False,
        )

    if len(matches) > 1:
        return json.dumps(
            {
                "ok": False,
                "error_code": "AMBIGUOUS_SERVICE",
                "message": f"服务名 '{service_name}' 匹配到多个候选",
                "candidates": [m[0] for m in matches],
            },
            ensure_ascii=False,
        )

    key, info = matches[0]
    return json.dumps(
        {
            "ok": True,
            "service": key,
            "app_container": info.get("app_container", ""),
            "db_container": info.get("db_container", ""),
        },
        ensure_ascii=False,
    )
