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
)
def check_network_connectivity(target_host: str, port: int, timeout: float = 2.0):
    """从宿主机测试 target_host:port 的 TCP 连通性。"""
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
    name="check_db_tcp_connectivity",
    permission="info",
    roles=["admin", "sre", "viewer"],
)
def check_db_tcp_connectivity(db_host: str, db_port: int, timeout: float = 2.0):
    """从宿主机测试 DB 端口连通性（仅 TCP 探测，不做认证）。"""
    return check_network_connectivity(db_host, db_port, timeout)


@register_tool(
    name="curl_http_endpoint",
    permission="moderate",
    roles=["admin", "sre", "viewer"],
)
def curl_http_endpoint(url: str, method: str = "HEAD", timeout: float = 5.0):
    """从宿主机发起 HTTP 请求，method 支持 HEAD/GET，返回结构化结果。"""
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
