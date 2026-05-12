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
    description="Check /actuator/health and return overall status with component details.",
)
def check_actuator_health(base_url: str = "", timeout: float = 3.0) -> str:
    """
    查询应用的健康检查端点（通常是 `/actuator/health`），返回结构化健康状态结果。

    功能解释:
    - 访问目标应用健康检查接口，获取整体状态（如 `UP` / `DOWN`）和组件级明细。
    - 自动统计本次请求延迟（毫秒）并返回 HTTP 状态码。
    - 发生异常时返回统一错误结构，便于上层流程自动处理。

    使用场景:
    - 故障初筛：先判断服务是否存活、依赖组件是否异常。
    - 发布后验证：确认新版本实例健康状态正常。
    - 自动巡检：作为低风险只读探测步骤的第一跳。

    参数说明:
    - `base_url` (str，可选，默认 `""`)：
      - 目标服务基地址，例如 `http://127.0.0.1:8080`。
      - 为空时自动使用运行时配置 `profile.app.host_base_url`。
    - `timeout` (float，可选，默认 `3.0`)：
      - HTTP 请求超时时间（秒）。
      - 建议范围 `1.0 ~ 10.0`，过小可能误判超时，过大可能拖慢诊断。

    必填字段:
    - 无。所有参数均可省略并使用默认值。

    调用方法:
    - 直接 `check_actuatro_health()`
    - 指定目标与超时：`check_actuatctuator_health(base_url="http://10.0.0.8:8080", timeout=5.0)`
    - 通过分发器：`dispatch_tool(action="check_actuator_health", params={"base_url":"http://10.0.0.8:8080","timeout":5.0})`

    返回关键字段:
    - 成功时常见字段：
      - `ok`：是否成功执行（true）。
      - `url`：实际访问的完整 URL。
      - `latency_ms`：请求耗时（毫秒）。
      - `http_status`：HTTP 状态码。
      - `status`：健康总状态。
      - `details`：组件/依赖健康明细。
      - `raw`：原始响应体（JSON 对象）。
    - 失败时常见字段：
      - `ok`：false。
      - `error_type` / `error`：错误类型与错误信息。
    """
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
    description="List metric names exposed by /actuator/metrics for discovery.",
)
def list_actuator_metrics(base_url: str = "", timeout: float = 3.0) -> str:
    """
    查询 `/actuator/metrics`，返回当前实例可读的指标名称列表。

    功能解释:
    - 拉取指标目录而非具体指标值，用于发现“有哪些可查询指标”。
    - 返回指标总数和名称列表（实现中会截断到前 300 项，避免返回过大）。

    使用场景:
    - 不确定指标名时先做探索，再调用 `get_actuator_metric` 精确查询。
    - 新服务接入时确认 Micrometer/Actuator 是否正常暴露指标。
    - 构建巡检或排障剧本时自动发现候选指标。

    参数说明:
    - `base_url` (str，可选，默认 `""`)：
      - 目标服务基地址；为空则回退到运行时默认地址。
    - `timeout` (float，可选，默认 `3.0`)：
      - 请求超时（秒）。

    必填字段:
    - 无。

    调用方法:
    - 默认调用：`list_actuator_metrics()`
    - 指定地址：`list_actuator_metrics(base_url="http://127.0.0.1:8080")`
    - 分发调用：`dispatch_tool(action="list_actuator_metrics", params={})`

    返回关键字段:
    - `ok`：调用是否成功。
    - `url`：实际请求地址。
    - `http_status`：HTTP 状态码。
    - `metric_count`：指标名称数量。
    - `names`：指标名列表（最多 300 个）。
    - 失败时附带 `error_type` 与 `error`。
    """
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
    description="Fetch one actuator metric with measurements and available tags.",
)
def get_actuator_metric(metric_name: str, base_url: str = "", timeout: float = 3.0) -> str:
    """
    查询单个指标详情端点 `/actuator/metrics/{metric_name}`，返回该指标的测量值与可用标签。

    功能解释:
    - 对指定指标名执行精确查询，读取 `measurements`（如 count/total/max 等）和标签维度信息。
    - 自动对指标名进行 URL 编码，避免特殊字符导致请求失败。
    - 参数非法（空指标名）时直接返回结构化错误，不发起网络请求。

    使用场景:
    - 已知指标名后获取实时值，例如请求数、错误数、JVM/线程/GC 相关指标。
    - 结合 `available_tags` 判断指标支持哪些过滤维度。
    - 指标异常时快速核对目标实例的当前观测值。

    参数说明:
    - `metric_name` (str，必填，无默认值)：
      - 目标指标名，例如 `jvm.threads.live`、`http.server.requests`。
      - 不能为空或纯空白字符串。
    - `base_url` (str，可选，默认 `""`)：
      - 目标服务基地址；为空时使用运行时默认地址。
    - `timeout` (float，可选，默认 `3.0`)：
      - 请求超时（秒）。

    必填字段:
    - `metric_name`。

    调用方法:
    - 直接调用：`get_actuator_metric(metric_name="jvm.threads.live")`
    - 指定实例：`get_actuator_metric(metric_name="http.server.requests", base_url="http://10.0.0.8:8080", timeout=5.0)`
    - 分发调用：`dispatch_tool(action="get_actuator_metric", params={"metric_name":"jvm.memory.used"})`

    返回关键字段:
    - 成功时：
      - `ok`、`url`、`http_status`、`metric_name`。
      - `measurements`：指标测量值数组。
      - `available_tags`：支持的标签维度与可选值。
      - `raw`：原始响应体。
    - 失败时：
      - `ok=false`，并返回 `error_type` 与 `error`。
      - 若 `metric_name` 为空，`error_type` 为 `InvalidArgument`。
    """
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
    description="Get /actuator/threaddump and summarize thread states with samples.",
)
def get_actuator_threaddump(base_url: str = "", timeout: float = 5.0) -> str:
    """
    查询 `/actuator/threaddump` 线程转储，并输出线程状态聚合与样本线程信息。

    功能解释:
    - 拉取完整线程信息后，按 `threadState` 统计各状态数量（如 RUNNABLE/BLOCKED/WAITING）。
    - 返回线程总数、状态分布和前 20 条线程样本，便于快速定位阻塞或堆积迹象。
    - 属于中等风险诊断动作（返回内容可能较大，且可能暴露实现细节）。

    使用场景:
    - CPU 飙高、接口超时、线程池耗尽时，判断是否出现阻塞/死锁/等待堆积。
    - 排查“服务存活但响应慢”问题，验证是否有大量 WAITING 或 BLOCKED 线程。
    - 结合健康检查和指标结果进行深度根因定位。

    参数说明:
    - `base_url` (str，可选，默认 `""`)：
      - 目标服务基地址；为空时使用运行时默认地址。
    - `timeout` (float，可选，默认 `5.0`)：
      - 请求超时（秒）。
      - 默认值高于普通查询，因为线程转储响应通常更大、更慢。

    必填字段:
    - 无。

    调用方法:
    - 默认调用：`get_actuator_threaddump()`
    - 指定超时：`get_actuator_threaddump(base_url="http://127.0.0.1:8080", timeout=8.0)`
    - 分发调用：`dispatch_tool(action="get_actuator_threaddump", params={"base_url":"http://127.0.0.1:8080"})`

    返回关键字段:
    - `ok`、`url`、`http_status`。
    - `thread_count`：线程总数。
    - `state_counts`：线程状态聚合统计。
    - `sample_threads`：线程样本（最多 20 条）。
    - 失败时返回 `error_type` 与 `error`。
    """
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
