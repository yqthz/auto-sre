import json
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict
from urllib.parse import quote

from app.agent.runtime_profile import get_runtime_profile
from app.agent.tools.security import register_tool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _join_url(base_url: str, endpoint: str) -> str:
    return f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"


def _http_json(url: str, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
        parsed = json.loads(body) if body else {}
        return {
            "http_status": int(resp.status),
            "body": parsed,
        }


def _result(ok: bool, **extra: Any) -> str:
    payload = {
        "queried_at": _now(),
        "ok": ok,
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


@register_tool(
    name="check_actuator_health",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["actuator"],
)
def check_actuator_health(base_url: str = "", timeout: float = 3.0) -> str:
    """Query the application /actuator/health endpoint and return structured health status."""
    profile = get_runtime_profile()
    selected_base_url = base_url or profile.app.host_base_url
    url = _join_url(selected_base_url, profile.app.health_endpoint)
    started = time.perf_counter()

    try:
        response = _http_json(url, timeout)
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        return _result(
            True,
            url=url,
            latency_ms=latency_ms,
            http_status=response.get("http_status"),
            status=body.get("status"),
            details=body.get("components") or body.get("details") or {},
            raw=body,
        )
    except Exception as e:
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        return _result(
            False,
            url=url,
            latency_ms=latency_ms,
            error_type=type(e).__name__,
            error=str(e),
        )


@register_tool(
    name="list_actuator_metrics",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["actuator"],
)
def list_actuator_metrics(base_url: str = "", timeout: float = 3.0) -> str:
    """Query /actuator/metrics and return available metric names."""
    profile = get_runtime_profile()
    selected_base_url = base_url or profile.app.host_base_url
    url = _join_url(selected_base_url, "/actuator/metrics")

    try:
        response = _http_json(url, timeout)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        names = body.get("names") if isinstance(body.get("names"), list) else []
        return _result(
            True,
            url=url,
            http_status=response.get("http_status"),
            metric_count=len(names),
            names=names[:300],
        )
    except Exception as e:
        return _result(False, url=url, error_type=type(e).__name__, error=str(e))


@register_tool(
    name="get_actuator_metric",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["actuator"],
)
def get_actuator_metric(metric_name: str, base_url: str = "", timeout: float = 3.0) -> str:
    """Query one /actuator/metrics/{metric_name} endpoint."""
    if not metric_name or not metric_name.strip():
        return _result(False, error_type="InvalidArgument", error="metric_name must not be empty")

    profile = get_runtime_profile()
    selected_base_url = base_url or profile.app.host_base_url
    url = _join_url(selected_base_url, f"/actuator/metrics/{quote(metric_name.strip(), safe='')}")

    try:
        response = _http_json(url, timeout)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        return _result(
            True,
            url=url,
            http_status=response.get("http_status"),
            metric_name=metric_name.strip(),
            measurements=body.get("measurements") or [],
            available_tags=body.get("availableTags") or [],
            raw=body,
        )
    except Exception as e:
        return _result(
            False,
            url=url,
            metric_name=metric_name.strip(),
            error_type=type(e).__name__,
            error=str(e),
        )


@register_tool(
    name="get_actuator_threaddump",
    permission="moderate",
    roles=["admin", "sre"],
    tags=["actuator"],
)
def get_actuator_threaddump(base_url: str = "", timeout: float = 5.0) -> str:
    """Query /actuator/threaddump only when thread count or blocking evidence requires it."""
    profile = get_runtime_profile()
    selected_base_url = base_url or profile.app.host_base_url
    url = _join_url(selected_base_url, "/actuator/threaddump")

    try:
        response = _http_json(url, timeout)
        body = response.get("body") if isinstance(response.get("body"), dict) else {}
        threads = body.get("threads") if isinstance(body.get("threads"), list) else []
        state_counts: Dict[str, int] = {}
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            state = str(thread.get("threadState") or "UNKNOWN")
            state_counts[state] = state_counts.get(state, 0) + 1
        return _result(
            True,
            url=url,
            http_status=response.get("http_status"),
            thread_count=len(threads),
            state_counts=state_counts,
            sample_threads=threads[:20],
        )
    except Exception as e:
        return _result(False, url=url, error_type=type(e).__name__, error=str(e))
