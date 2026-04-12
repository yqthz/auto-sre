import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus
from urllib.request import urlopen

from app.agent.tools.security import register_tool
from app.core.config import settings

ALERT_METRICS_MAP = {
    "HighMemoryUsage": [
        ("heap_usage_percent", 'jvm_memory_used_bytes{area="heap"} / jvm_memory_max_bytes{area="heap"} * 100'),
        ("gc_frequency", 'rate(jvm_gc_pause_seconds_count[5m]) * 60'),
        ("gc_pause_p99", 'histogram_quantile(0.99, jvm_gc_pause_seconds_bucket)'),
    ],
    "HighErrorRate": [
        ("error_rate", 'rate(http_server_requests_seconds_count{status=~"5.."}[5m])'),
        ("request_volume", 'rate(http_server_requests_seconds_count[5m])'),
        ("p99_latency", 'histogram_quantile(0.99, http_server_requests_seconds_bucket)'),
    ],
    "HighCPUUsage": [
        ("cpu_usage", "process_cpu_usage * 100"),
        ("thread_count", "jvm_threads_live_threads"),
    ],
    "HighDatabaseConnections": [
        ("active_connections", "hikaricp_connections_active"),
        ("pending_connections", "hikaricp_connections_pending"),
        ("timeout_count", "hikaricp_connections_timeout_total"),
    ],
    "InstanceDown": [
        ("up", 'up{instance="{instance}"}'),
    ],
}

METRIC_LABELS = {
    "heap_usage_percent": "堆内存使用率",
    "gc_frequency": "GC 频率",
    "gc_pause_p99": "GC 暂停 P99",
    "error_rate": "5xx 错误率",
    "request_volume": "请求量",
    "p99_latency": "P99 延迟",
    "cpu_usage": "CPU 使用率",
    "thread_count": "线程数",
    "active_connections": "活跃连接数",
    "pending_connections": "等待连接数",
    "timeout_count": "连接超时数",
    "up": "实例存活状态",
}

METRIC_UNITS = {
    "heap_usage_percent": "%",
    "gc_frequency": "次/分钟",
    "gc_pause_p99": "ms",
    "error_rate": "req/s",
    "request_volume": "req/s",
    "p99_latency": "s",
    "cpu_usage": "%",
    "thread_count": "count",
    "active_connections": "count",
    "pending_connections": "count",
    "timeout_count": "count",
    "up": "bool",
}


def _prometheus_query(promql: str) -> Dict[str, Any]:
    base_url = settings.PROMETHEUS_URL.rstrip("/")
    url = f"{base_url}/api/v1/query?query={quote_plus(promql)}"
    with urlopen(url, timeout=8) as response:
        payload = response.read().decode("utf-8", errors="ignore")
    return json.loads(payload)


def _extract_numeric_value(data: Dict[str, Any]) -> Optional[float]:
    if data.get("status") != "success":
        return None
    result = ((data.get("data") or {}).get("result")) or []
    if not result:
        return None
    sample = result[0].get("value")
    if not sample or len(sample) < 2:
        return None
    try:
        return float(sample[1])
    except (TypeError, ValueError):
        return None


@register_tool(
    name="query_prometheus_metrics",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["prometheus"],
)
def query_prometheus_metrics(alert_name: str, instance: str) -> str:
    """
    Query Prometheus metrics snapshot by alert type and instance.
    """
    metric_defs = ALERT_METRICS_MAP.get(alert_name)
    if not metric_defs:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "metrics": [],
                "warning": f"unsupported alert_name: {alert_name}",
            },
            ensure_ascii=False,
        )

    metrics: List[Dict[str, Any]] = []
    for metric_name, template in metric_defs:
        promql = template.replace("{instance}", instance or "")
        value: Optional[float] = None
        error: Optional[str] = None
        try:
            data = _prometheus_query(promql)
            value = _extract_numeric_value(data)
        except Exception as e:
            error = str(e)

        item: Dict[str, Any] = {
            "name": metric_name,
            "label": METRIC_LABELS.get(metric_name, metric_name),
            "value": None if value is None else str(round(value, 4)),
            "unit": METRIC_UNITS.get(metric_name, ""),
            "promql": promql,
        }
        if error:
            item["error"] = error
        metrics.append(item)

    return json.dumps(
        {
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "alert_name": alert_name,
            "instance": instance,
            "metrics": metrics,
        },
        ensure_ascii=False,
    )
