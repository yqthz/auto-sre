import json
import socket
import time
import urllib.request

from app.agent.tools.security import register_tool


def _json_result(tool: str, ok: bool, **extra):
    payload = {
        "tool": tool,
        "ok": ok,
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


@register_tool(
    name="check_network_connectivity",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["network"],
)
def check_network_connectivity(target_host: str, port: int, timeout: float = 2.0):
    """
    从宿主机发起 TCP 建连探测，验证目标主机端口是否可达。

    功能解释:
    - 使用 `socket.create_connection` 尝试连接 `target_host:port`。
    - 仅验证 TCP 三次握手是否能完成，不验证应用层协议。
    - 返回统一 JSON，便于 agent 继续判断是“网络问题”还是“应用问题”。

    使用场景:
    - 目标服务超时、拒绝连接、无法访问时先排除基础网络问题。
    - 调试数据库、中间件、外部依赖端口是否从当前宿主可达。
    - 在执行 HTTP/SQL 诊断前做第一层连通性确认。

    参数说明:
    - `target_host` (str，必填)：
      - 目标主机名或 IP。
    - `port` (int，必填)：
      - 目标端口号。
    - `timeout` (float，可选，默认 `2.0`)：
      - 建连超时时间（秒）。
      - 过小可能导致误判，过大可能拖慢排障节奏。

    必填字段:
    - `target_host`
    - `port`

    调用方法:
    - 直接调用：`check_network_connectivity(target_host="10.0.0.8", port=3306)`
    - 指定超时：`check_network_connectivity(target_host="10.0.0.8", port=3306, timeout=3.5)`
    - 分发调用：`dispatch_tool(action="check_network_connectivity", params={"target_host":"10.0.0.8","port":3306,"timeout":3.5})`

    返回关键字段:
    - `tool`：工具名。
    - `ok`：是否连通。
    - `target_host` / `port`：目标地址。
    - `timeout_s`：使用的超时值。
    - `latency_ms`：探测耗时。
    - `status`：`reachable` 或 `unreachable`。
    - 失败时附带 `error_type` 与 `error`。
    """
    start = time.perf_counter()
    try:
        with socket.create_connection((target_host, int(port)), timeout=float(timeout)):
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            return _json_result(
                "check_network_connectivity",
                True,
                target_host=target_host,
                port=int(port),
                timeout_s=float(timeout),
                latency_ms=latency_ms,
                status="reachable",
            )
    except Exception as e:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return _json_result(
            "check_network_connectivity",
            False,
            target_host=target_host,
            port=int(port),
            timeout_s=float(timeout),
            latency_ms=latency_ms,
            status="unreachable",
            error_type=type(e).__name__,
            error=str(e),
        )


@register_tool(
    name="curl_http_endpoint",
    permission="moderate",
    roles=["admin", "sre", "viewer"],
    tags=["network"],
)
def curl_http_endpoint(url: str, method: str = "HEAD", timeout: float = 5.0):
    """
    从宿主机发起 HTTP 探测请求，验证 URL 是否可达并返回状态码。

    功能解释:
    - 支持 `HEAD` 和 `GET` 两种方法。
    - 当 `HEAD` 失败时会自动回退到 `GET` 再尝试一次。
    - 返回实际使用的方法、HTTP 状态码、耗时以及错误信息。

    使用场景:
    - 验证健康检查地址是否可访问。
    - 排查“端口通了但 HTTP 不通”的问题。
    - 确认网关、反向代理、外部依赖 URL 是否正常响应。

    参数说明:
    - `url` (str，必填)：
      - 完整 HTTP/HTTPS 地址。
    - `method` (str，可选，默认 `HEAD`)：
      - 请求方法，仅支持 `HEAD` 或 `GET`，大小写不敏感。
    - `timeout` (float，可选，默认 `5.0`)：
      - 请求超时时间（秒）。

    必填字段:
    - `url`

    调用方法:
    - 直接调用：`curl_http_endpoint(url="http://127.0.0.1:8080/actuator/health")`
    - 指定方法：`curl_http_endpoint(url="https://example.com", method="GET", timeout=5.0)`
    - 分发调用：`dispatch_tool(action="curl_http_endpoint", params={"url":"https://example.com","method":"GET","timeout":5.0})`

    返回关键字段:
    - `tool`：工具名。
    - `ok`：是否成功。
    - `url`：请求地址。
    - `method`：入参方法。
    - `used_method`：实际使用的方法。
    - `timeout_s`：请求超时。
    - `latency_ms`：请求耗时。
    - `http_status`：HTTP 状态码。
    - 失败时附带 `error_type` / `error`，HEAD 回退时附带 `fallback_from` 或 `head_error`。
    """
    method = (method or "HEAD").upper()
    if method not in ("HEAD", "GET"):
        return _json_result(
            "curl_http_endpoint",
            False,
            url=url,
            method=method,
            timeout_s=float(timeout),
            error_type="InvalidMethod",
            error="method must be HEAD or GET",
        )

    start = time.perf_counter()

    def _do_request(selected_method: str):
        req = urllib.request.Request(url=url, method=selected_method)
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            return int(resp.status), selected_method

    try:
        status_code, used_method = _do_request(method)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return _json_result(
            "curl_http_endpoint",
            True,
            url=url,
            method=method,
            used_method=used_method,
            timeout_s=float(timeout),
            latency_ms=latency_ms,
            http_status=status_code,
        )
    except Exception as first_err:
        if method == "HEAD":
            try:
                status_code, used_method = _do_request("GET")
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                return _json_result(
                    "curl_http_endpoint",
                    True,
                    url=url,
                    method=method,
                    used_method=used_method,
                    timeout_s=float(timeout),
                    latency_ms=latency_ms,
                    http_status=status_code,
                    fallback_from="HEAD",
                )
            except Exception as second_err:
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                return _json_result(
                    "curl_http_endpoint",
                    False,
                    url=url,
                    method=method,
                    timeout_s=float(timeout),
                    latency_ms=latency_ms,
                    error_type=type(second_err).__name__,
                    error=str(second_err),
                    head_error=f"{type(first_err).__name__}: {first_err}",
                )

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return _json_result(
            "curl_http_endpoint",
            False,
            url=url,
            method=method,
            timeout_s=float(timeout),
            latency_ms=latency_ms,
            error_type=type(first_err).__name__,
            error=str(first_err),
        )
