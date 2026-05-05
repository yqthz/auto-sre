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
        ("gc_pause_rate", 'rate(jvm_gc_pause_seconds_sum[5m])'),
    ],
    "HighErrorRate": [
        ("error_rate", 'sum(rate(http_server_requests_seconds_count{status=~"5.."}[5m])) by (uri)'),
        ("request_volume", 'sum(rate(http_server_requests_seconds_count[5m])) by (uri)'),
        ("p99_latency", 'histogram_quantile(0.99, sum(rate(http_server_requests_seconds_bucket[5m])) by (le, uri))'),
    ],
    "HighCPUUsage": [
        ("cpu_usage", "(system_cpu_usage or process_cpu_usage) * 100"),
        ("thread_count", "jvm_threads_live_threads"),
    ],
    "HighDatabaseConnections": [
        ("connection_usage_percent", "((hikari_connections_active / hikari_connections_max) or (hikaricp_connections_active / hikaricp_connections_max)) * 100"),
        ("active_connections", "hikari_connections_active or hikaricp_connections_active"),
        ("max_connections", "hikari_connections_max or hikaricp_connections_max"),
        ("pending_connections", "hikari_connections_pending or hikaricp_connections_pending"),
        ("timeout_count", "hikari_connections_timeout_total or hikaricp_connections_timeout_total"),
    ],
    "InstanceDown": [
        ("up", 'up{instance="{instance}"}'),
    ],
    "LongGC": [
        ("gc_pause_rate", "rate(jvm_gc_pause_seconds_sum[5m])"),
        ("gc_frequency", "rate(jvm_gc_pause_seconds_count[5m]) * 60"),
        ("heap_usage_percent", 'jvm_memory_used_bytes{area="heap"} / jvm_memory_max_bytes{area="heap"} * 100'),
    ],
    "HighThreadCount": [
        ("thread_count", "jvm_threads_live_threads"),
        ("thread_daemon_count", "jvm_threads_daemon_threads"),
        ("p95_latency", "histogram_quantile(0.95, sum(rate(http_server_requests_seconds_bucket[5m])) by (le, uri))"),
    ],
    "HighResponseTime": [
        ("p95_latency", "histogram_quantile(0.95, sum(rate(http_server_requests_seconds_bucket[5m])) by (le, uri))"),
        ("p99_latency", "histogram_quantile(0.99, sum(rate(http_server_requests_seconds_bucket[5m])) by (le, uri))"),
        ("connection_usage_percent", "((hikari_connections_active / hikari_connections_max) or (hikaricp_connections_active / hikaricp_connections_max)) * 100"),
        ("gc_pause_rate", "rate(jvm_gc_pause_seconds_sum[5m])"),
        ("thread_count", "jvm_threads_live_threads"),
    ],
}

METRIC_LABELS = {
    "heap_usage_percent": "堆内存使用率",
    "gc_frequency": "GC 频率",
    "gc_pause_rate": "GC 暂停耗时速率",
    "error_rate": "5xx 错误率",
    "request_volume": "请求量",
    "p95_latency": "P95 延迟",
    "p99_latency": "P99 延迟",
    "cpu_usage": "CPU 使用率",
    "thread_count": "线程数",
    "thread_daemon_count": "守护线程数",
    "connection_usage_percent": "连接池使用率",
    "active_connections": "活跃连接数",
    "max_connections": "最大连接数",
    "pending_connections": "等待连接数",
    "timeout_count": "连接超时数",
    "up": "实例存活状态",
}

METRIC_UNITS = {
    "heap_usage_percent": "%",
    "gc_frequency": "次/分钟",
    "gc_pause_rate": "s/s",
    "error_rate": "req/s",
    "request_volume": "req/s",
    "p95_latency": "s",
    "p99_latency": "s",
    "cpu_usage": "%",
    "thread_count": "count",
    "thread_daemon_count": "count",
    "connection_usage_percent": "%",
    "active_connections": "count",
    "max_connections": "count",
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


def _prometheus_get(path: str) -> Dict[str, Any]:
    base_url = settings.PROMETHEUS_URL.rstrip("/")
    url = f"{base_url}{path}"
    with urlopen(url, timeout=8) as response:
        payload = response.read().decode("utf-8", errors="ignore")
    return json.loads(payload)


def _prometheus_query_range(promql: str, start_ts: int, end_ts: int, step: int) -> Dict[str, Any]:
    base_url = settings.PROMETHEUS_URL.rstrip("/")
    url = (
        f"{base_url}/api/v1/query_range?query={quote_plus(promql)}"
        f"&start={start_ts}&end={end_ts}&step={step}"
    )
    with urlopen(url, timeout=10) as response:
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


def _extract_range_series(data: Dict[str, Any], limit_points: int = 120) -> List[Dict[str, Any]]:
    if data.get("status") != "success":
        return []
    result = ((data.get("data") or {}).get("result")) or []
    if not result:
        return []

    series_list: List[Dict[str, Any]] = []
    for item in result:
        metric = item.get("metric") or {}
        values = item.get("values") or []
        if limit_points > 0 and len(values) > limit_points:
            values = values[-limit_points:]

        points: List[List[str]] = []
        for pair in values:
            if not pair or len(pair) < 2:
                continue
            points.append([str(pair[0]), str(pair[1])])

        series_list.append({"metric": metric, "points": points})
    return series_list


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


@register_tool(
    name="query_prometheus_range_metrics",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["prometheus"],
)
def query_prometheus_range_metrics(
    alert_name: str,
    instance: str,
    start_time: str,
    end_time: str,
    step_seconds: int = 30,
) -> str:
    """
    Query Prometheus range metrics by alert type and instance.
    start_time/end_time must be ISO-8601, e.g. 2026-04-30T10:00:00Z.
    """
    metric_defs = ALERT_METRICS_MAP.get(alert_name)
    if not metric_defs:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "series": [],
                "warning": f"unsupported alert_name: {alert_name}",
            },
            ensure_ascii=False,
        )

    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "series": [],
                "warning": "invalid start_time or end_time, expected ISO-8601",
            },
            ensure_ascii=False,
        )

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    else:
        start_dt = start_dt.astimezone(timezone.utc)

    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    else:
        end_dt = end_dt.astimezone(timezone.utc)

    if end_dt <= start_dt:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "series": [],
                "warning": "end_time must be later than start_time",
            },
            ensure_ascii=False,
        )

    step_seconds = max(5, min(step_seconds, 3600))
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    series: List[Dict[str, Any]] = []
    for metric_name, template in metric_defs:
        promql = template.replace("{instance}", instance or "")
        points: List[Dict[str, Any]] = []
        error: Optional[str] = None
        try:
            data = _prometheus_query_range(promql, start_ts, end_ts, step_seconds)
            points = _extract_range_series(data)
        except Exception as e:
            error = str(e)

        item: Dict[str, Any] = {
            "name": metric_name,
            "label": METRIC_LABELS.get(metric_name, metric_name),
            "unit": METRIC_UNITS.get(metric_name, ""),
            "promql": promql,
            "series": points,
        }
        if error:
            item["error"] = error
        series.append(item)

    return json.dumps(
        {
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "alert_name": alert_name,
            "instance": instance,
            "time_range": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "step_seconds": step_seconds,
            },
            "series": series,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="query_prometheus_by_promql",
    permission="moderate",
    roles=["admin", "sre"],
    tags=["prometheus"],
)
def query_prometheus_by_promql(
    promql: str,
    mode: str = "instant",
    start_time: str = "",
    end_time: str = "",
    step_seconds: int = 30,
) -> str:
    """
    Query Prometheus directly by PromQL.
    mode supports: instant | range.
    For range mode, provide start_time/end_time in ISO-8601 and optional step_seconds.
    """
    mode = (mode or "instant").strip().lower()
    if mode not in {"instant", "range"}:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "warning": f"unsupported mode: {mode}",
            },
            ensure_ascii=False,
        )

    if mode == "instant":
        try:
            data = _prometheus_query(promql)
        except Exception as e:
            return json.dumps(
                {
                    "queried_at": datetime.now(timezone.utc).isoformat(),
                    "promql": promql,
                    "mode": mode,
                    "error": str(e),
                },
                ensure_ascii=False,
            )

        result = ((data.get("data") or {}).get("result")) or []
        compact: List[Dict[str, Any]] = []
        for item in result[:50]:
            sample = item.get("value") or []
            compact.append(
                {
                    "metric": item.get("metric") or {},
                    "value": sample[1] if len(sample) > 1 else None,
                    "timestamp": sample[0] if len(sample) > 0 else None,
                }
            )
        payload: Dict[str, Any] = {"result_type": "vector", "result": compact}

        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "promql": promql,
                "mode": mode,
                "status": data.get("status"),
                "data": payload,
            },
            ensure_ascii=False,
        )

    if not start_time or not end_time:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "promql": promql,
                "mode": mode,
                "warning": "start_time and end_time are required for range mode",
            },
            ensure_ascii=False,
        )

    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "promql": promql,
                "mode": mode,
                "warning": "invalid start_time or end_time, expected ISO-8601",
            },
            ensure_ascii=False,
        )

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    else:
        start_dt = start_dt.astimezone(timezone.utc)

    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    else:
        end_dt = end_dt.astimezone(timezone.utc)

    if end_dt <= start_dt:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "promql": promql,
                "mode": mode,
                "warning": "end_time must be later than start_time",
            },
            ensure_ascii=False,
        )

    step_seconds = max(5, min(step_seconds, 3600))
    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    try:
        data = _prometheus_query_range(promql, start_ts, end_ts, step_seconds)
    except Exception as e:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "promql": promql,
                "mode": mode,
                "time_range": {
                    "start": start_dt.isoformat(),
                    "end": end_dt.isoformat(),
                    "step_seconds": step_seconds,
                },
                "error": str(e),
            },
            ensure_ascii=False,
        )

    payload = {
        "result_type": (data.get("data") or {}).get("resultType"),
        "result": _extract_range_series(data),
    }

    return json.dumps(
        {
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "promql": promql,
            "mode": mode,
            "time_range": {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "step_seconds": step_seconds,
            },
            "status": data.get("status"),
            "data": payload,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="query_prometheus_targets_health",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["prometheus"],
)
def query_prometheus_targets_health(job: str = "", instance: str = "") -> str:
    """
    Query target/scrape health metrics to identify collection pipeline issues.
    """
    label_filters: List[str] = []
    if job:
        label_filters.append(f'job="{job}"')
    if instance:
        label_filters.append(f'instance="{instance}"')
    selector = "{" + ",".join(label_filters) + "}" if label_filters else ""

    checks = [
        ("up", f"up{selector}", "实例存活状态", "bool"),
        (
            "scrape_duration_seconds",
            f"scrape_duration_seconds{selector}",
            "抓取耗时",
            "s",
        ),
        (
            "scrape_samples_post_metric_relabeling",
            f"scrape_samples_post_metric_relabeling{selector}",
            "抓取样本数",
            "count",
        ),
        (
            "scrape_series_added",
            f"scrape_series_added{selector}",
            "新增时序数",
            "count",
        ),
    ]

    metrics: List[Dict[str, Any]] = []
    for name, promql, label, unit in checks:
        error: Optional[str] = None
        values: List[Dict[str, Any]] = []
        try:
            data = _prometheus_query(promql)
            result = ((data.get("data") or {}).get("result")) or []
            for row in result[:50]:
                sample = row.get("value") or []
                values.append(
                    {
                        "metric": row.get("metric") or {},
                        "value": sample[1] if len(sample) > 1 else None,
                        "timestamp": sample[0] if len(sample) > 0 else None,
                    }
                )
        except Exception as e:
            error = str(e)

        item: Dict[str, Any] = {
            "name": name,
            "label": label,
            "unit": unit,
            "promql": promql,
            "values": values,
        }
        if error:
            item["error"] = error
        metrics.append(item)

    return json.dumps(
        {
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "job": job,
            "instance": instance,
            "metrics": metrics,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="query_prometheus_targets",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["prometheus"],
)
def query_prometheus_targets(job: str = "", instance: str = "") -> str:
    """Query Prometheus /api/v1/targets and return active target health details."""
    try:
        data = _prometheus_get("/api/v1/targets")
    except Exception as e:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "error": str(e),
                "targets": [],
            },
            ensure_ascii=False,
        )

    active = (((data.get("data") or {}).get("activeTargets")) or []) if isinstance(data, dict) else []
    targets: List[Dict[str, Any]] = []
    for item in active:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels") or {}
        discovered = item.get("discoveredLabels") or {}
        target_job = str(labels.get("job") or discovered.get("__meta_docker_container_label_com_docker_compose_service") or "")
        target_instance = str(labels.get("instance") or "")
        if job and target_job != job:
            continue
        if instance and target_instance != instance:
            continue
        targets.append(
            {
                "job": target_job,
                "instance": target_instance,
                "health": item.get("health"),
                "last_scrape": item.get("lastScrape"),
                "last_scrape_duration": item.get("lastScrapeDuration"),
                "last_error": item.get("lastError"),
                "scrape_url": item.get("scrapeUrl"),
                "labels": labels,
            }
        )

    return json.dumps(
        {
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "ok": data.get("status") == "success" if isinstance(data, dict) else False,
            "job": job,
            "instance": instance,
            "target_count": len(targets),
            "targets": targets,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="query_prometheus_alerts",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["prometheus"],
)
def query_prometheus_alerts(alert_name: str = "", state: str = "") -> str:
    """Query Prometheus /api/v1/alerts and return current alert states."""
    try:
        data = _prometheus_get("/api/v1/alerts")
    except Exception as e:
        return json.dumps(
            {
                "queried_at": datetime.now(timezone.utc).isoformat(),
                "ok": False,
                "error": str(e),
                "alerts": [],
            },
            ensure_ascii=False,
        )

    raw_alerts = (((data.get("data") or {}).get("alerts")) or []) if isinstance(data, dict) else []
    alerts: List[Dict[str, Any]] = []
    for item in raw_alerts:
        if not isinstance(item, dict):
            continue
        labels = item.get("labels") or {}
        current_name = str(labels.get("alertname") or "")
        current_state = str(item.get("state") or "")
        if alert_name and current_name != alert_name:
            continue
        if state and current_state != state:
            continue
        alerts.append(
            {
                "state": current_state,
                "alertname": current_name,
                "labels": labels,
                "annotations": item.get("annotations") or {},
                "active_at": item.get("activeAt"),
                "value": item.get("value"),
            }
        )

    return json.dumps(
        {
            "queried_at": datetime.now(timezone.utc).isoformat(),
            "ok": data.get("status") == "success" if isinstance(data, dict) else False,
            "alert_name": alert_name,
            "state": state,
            "alert_count": len(alerts),
            "alerts": alerts,
        },
        ensure_ascii=False,
    )
