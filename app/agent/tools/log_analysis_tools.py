import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.agent.runtime_profile import get_runtime_profile
from app.agent.tools.security import register_tool
from app.core.logger import logger

MAX_SCAN_LINES = 20000
MAX_OUTPUT_ENTRIES = 20
MAX_CONTEXT_LINES = 20
MAX_CONTEXT_MATCHES = 10
MAX_CONTEXT_OUTPUT_CHARS = 8000
MAX_OVERVIEW_PATTERNS = 8
MAX_OVERVIEW_ITEMS = 10

TIMESTAMP_PATTERNS = [
    re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"),
    re.compile(r"(?P<ts>\d{2}:\d{2}:\d{2})"),
]
LEVEL_PATTERN = re.compile(r"\b(ERROR|WARN|WARNING)\b", re.IGNORECASE)
LOG_PATTERN = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*\|\s*"
    r"(?P<level>[A-Z]+)\s*\|\s*"
    r"(?P<thread>[^|]*)\|\s*"
    r"traceId=(?P<trace_id>[^|]*)\|\s*"
    r"userId=(?P<user_id>[^|]*)\|\s*"
    r"sessionId=(?P<session_id>[^|]*)\|\s*"
    r"(?P<logger>[^|]*)\|\s*"
    r"(?P<message>.*)$"
)
MESSAGE_FIELD_PATTERN = re.compile(r"(?P<key>method|uri|query|status|costMs|ip|userAgent|remoteAddr)=([^,]+)")


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _target_timezone():
    try:
        return ZoneInfo(get_runtime_profile().timezone)
    except Exception:
        return timezone.utc


def _local_to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_target_timezone())
    return dt.astimezone(timezone.utc)


def _parse_line_timestamp(line: str, base_date: datetime) -> Optional[datetime]:
    """从日志中提取时间戳"""
    for pattern in TIMESTAMP_PATTERNS:
        match = pattern.search(line)
        if not match:
            continue
        raw = match.group("ts")
        if len(raw) == 8:  # HH:MM:SS
            try:
                t = datetime.strptime(raw, "%H:%M:%S").time()
                local_base = base_date.astimezone(_target_timezone())
                return datetime.combine(local_base.date(), t, tzinfo=_target_timezone()).astimezone(timezone.utc)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(raw.replace(" ", "T"))
            return _local_to_utc(parsed)
        except ValueError:
            continue
    return None


def _parse_message_fields(message: str) -> Dict[str, object]:
    fields: Dict[str, object] = {}
    for match in MESSAGE_FIELD_PATTERN.finditer(message or ""):
        key = match.group("key")
        raw = match.group(2).strip()
        if key in {"status", "costMs"}:
            try:
                fields[key] = int(raw)
            except ValueError:
                fields[key] = raw
        else:
            fields[key] = raw
    return fields


def _parse_log_line(line: str, base_date: datetime) -> Dict[str, object]:
    """日志解析"""
    match = LOG_PATTERN.match(line.rstrip("\n"))

    # 匹配失败
    if not match:
        logger.error("parse log line match failed")

        ts = _parse_line_timestamp(line, base_date)
        level_match = LEVEL_PATTERN.search(line)
        return {
            "timestamp": ts,
            "level": (level_match.group(1).upper() if level_match else ""),
            "message": _normalize_message(line),
            "raw": line.rstrip("\n"),
            "parse_error": True,
        }

    groups = match.groupdict()
    ts = _parse_line_timestamp(groups.get("ts") or "", base_date)
    message = (groups.get("message") or "").strip()
    parsed: Dict[str, object] = {
        "timestamp": ts,
        "level": (groups.get("level") or "").strip(),
        "thread": (groups.get("thread") or "").strip(),
        "traceId": (groups.get("trace_id") or "").strip(),
        "userId": (groups.get("user_id") or "").strip(),
        "sessionId": (groups.get("session_id") or "").strip(),
        "logger": (groups.get("logger") or "").strip(),
        "message": message,
        "raw": line.rstrip("\n"),
        "parse_error": False,
    }
    parsed.update(_parse_message_fields(message))
    return parsed


def _normalize_message(line: str) -> str:
    msg = line.strip()
    # Remove leading timestamp and log level noise to improve aggregation.
    msg = re.sub(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*", "", msg)
    msg = re.sub(r"^\d{2}:\d{2}:\d{2}\s*", "", msg)
    msg = re.sub(r"\b(ERROR|WARN|WARNING)\b[:\s-]*", "", msg, flags=re.IGNORECASE)
    msg = re.sub(r"\s+", " ", msg).strip()
    return msg[:200]


# TODO: 
def _resolve_log_files(alert_dt: datetime, window_minutes: int) -> Tuple[List[Path], List[str]]:
    """查找日志文件"""
    profile = get_runtime_profile()
    log_dir = profile.app.log_dir
    patterns = profile.app.log_patterns
    # 计算告警时间窗口
    # start = alert_dt - timedelta(minutes=window_minutes)
    # end = alert_dt + timedelta(minutes=window_minutes)

    checked: List[str] = []
    existing_map: Dict[str, Path] = {}
    if not log_dir.exists():
        return [], [str(log_dir)]

    for pattern in patterns:
        checked.append(str(log_dir / pattern))
        for path in log_dir.glob(pattern):
            if path.exists() and path.is_file():
                existing_map[str(path)] = path

    existing = sorted(existing_map.values(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    # Keep the scan bounded but include current and recent rotated files.
    return existing[:20], checked


def _read_tail_lines(path: Path, limit: int) -> List[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return []
    if len(lines) > limit:
        return lines[-limit:]
    return lines


@register_tool(
    name="overview_log_issues",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
    description="First-pass log issue overview around an alert time.",
)
def overview_log_issues(alert_time: str, window_minutes: int = 5) -> str:
    """
    首轮日志问题总览，给 agent 快速建立排查方向。

    功能解释:
    - 扫描告警时间窗口内的日志，返回高信号聚合摘要。
    - 汇总 ERROR/WARN、HTTP 5xx、慢请求、受影响 URI 和可疑 traceId。

    使用场景:
    - 自动诊断开始时，先判断日志侧是否存在明显异常。
    - 需要一个轻量结果辅助形成初始根因假设。

    参数说明:
    - `alert_time` (str，必填)：告警时间，建议 ISO-8601。
    - `window_minutes` (int，可选，默认 `5`)：告警前后窗口，范围会限制在 `1 ~ 60`。

    必填字段:
    - `alert_time`

    调用方法:
    - `overview_log_issues(alert_time="2026-05-11T10:00:00+08:00")`
    - `dispatch_tool(action="log.overview_log_issues", params={"alert_time":"2026-05-11T10:00:00+08:00","window_minutes":10})`

    返回关键字段:
    - `summary`：首轮计数摘要。
    - `top_error_patterns`：高频 ERROR/WARN 模式。
    - `top_affected_uris`：按 5xx、慢请求、错误数排序的 URI。
    - `top_suspicious_traces`：按错误、5xx、慢请求排序的 traceId。
    - `sample_evidence`：可读样例日志。
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    window_minutes = max(1, min(window_minutes, 60))
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    files, checked = _resolve_log_files(alert_dt, window_minutes)
    if not files:
        return json.dumps(
            {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "time_range": {"from": start.isoformat(), "to": end.isoformat()},
                "summary": {
                    "files_scanned": 0,
                    "lines_scanned": 0,
                    "lines_in_window": 0,
                    "error_count": 0,
                    "warn_count": 0,
                    "http_5xx_count": 0,
                    "slow_request_count": 0,
                    "affected_uri_count": 0,
                    "trace_count": 0,
                    "parse_error_count": 0,
                },
                "top_error_patterns": [],
                "top_affected_uris": [],
                "top_suspicious_traces": [],
                "sample_evidence": [],
                "warning": "no log files found for alert window",
                "checked_files": checked,
            },
            ensure_ascii=False,
        )

    error_patterns: Dict[Tuple[str, str], Dict[str, object]] = defaultdict(lambda: {"count": 0, "first_seen": None})
    uri_buckets: Dict[str, Dict[str, object]] = {}
    trace_buckets: Dict[str, Dict[str, object]] = {}
    sample_evidence: List[Dict[str, object]] = []

    lines_scanned = 0
    lines_in_window = 0
    error_count = 0
    warn_count = 0
    http_5xx_count = 0
    slow_request_count = 0
    parse_error_count = 0

    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        lines_scanned += len(lines)
        for line in lines:
            parsed_line = _parse_log_line(line, alert_dt)
            line_time = parsed_line.get("timestamp")
            if line_time and not (start <= line_time <= end):
                continue

            lines_in_window += 1
            if parsed_line.get("parse_error") and (parsed_line.get("level") or LEVEL_PATTERN.search(line)):
                parse_error_count += 1

            level_raw = str(parsed_line.get("level") or "").upper()
            level = "WARN" if level_raw == "WARNING" else level_raw
            message = str(parsed_line.get("message") or _normalize_message(line)).strip()
            status = parsed_line.get("status")
            cost_ms = parsed_line.get("costMs")
            uri = str(parsed_line.get("uri") or "").strip()
            trace_id = str(parsed_line.get("traceId") or "").strip()
            time_text = line_time.isoformat() if isinstance(line_time, datetime) else alert_dt.isoformat()

            if level == "ERROR":
                error_count += 1
            elif level == "WARN":
                warn_count += 1

            is_5xx = isinstance(status, int) and status >= 500
            is_slow = isinstance(cost_ms, int) and cost_ms >= 1000
            if is_5xx:
                http_5xx_count += 1
            if is_slow:
                slow_request_count += 1

            if level in {"ERROR", "WARN"} and message:
                key = (level, message[:300])
                error_patterns[key]["count"] = int(error_patterns[key]["count"]) + 1
                if error_patterns[key]["first_seen"] is None:
                    error_patterns[key]["first_seen"] = time_text

            if uri:
                current_uri = uri_buckets.get(uri)
                if current_uri is None:
                    current_uri = {
                        "uri": uri,
                        "request_count": 0,
                        "error_count": 0,
                        "warn_count": 0,
                        "http_5xx_count": 0,
                        "slow_count": 0,
                        "max_cost_ms": 0,
                        "status_codes": defaultdict(int),
                    }
                    uri_buckets[uri] = current_uri
                current_uri["request_count"] = int(current_uri["request_count"]) + 1
                if level == "ERROR":
                    current_uri["error_count"] = int(current_uri["error_count"]) + 1
                elif level == "WARN":
                    current_uri["warn_count"] = int(current_uri["warn_count"]) + 1
                if is_5xx:
                    current_uri["http_5xx_count"] = int(current_uri["http_5xx_count"]) + 1
                if is_slow:
                    current_uri["slow_count"] = int(current_uri["slow_count"]) + 1
                if isinstance(cost_ms, int):
                    current_uri["max_cost_ms"] = max(int(current_uri["max_cost_ms"]), cost_ms)
                if isinstance(status, int):
                    current_uri["status_codes"][str(status)] += 1

            if trace_id:
                current_trace = trace_buckets.get(trace_id)
                if current_trace is None:
                    current_trace = {
                        "traceId": trace_id,
                        "total_lines": 0,
                        "error_count": 0,
                        "warn_count": 0,
                        "http_5xx_count": 0,
                        "slow_count": 0,
                        "max_cost_ms": 0,
                        "uris": set(),
                        "sample_message": "",
                    }
                    trace_buckets[trace_id] = current_trace
                current_trace["total_lines"] = int(current_trace["total_lines"]) + 1
                if level == "ERROR":
                    current_trace["error_count"] = int(current_trace["error_count"]) + 1
                elif level == "WARN":
                    current_trace["warn_count"] = int(current_trace["warn_count"]) + 1
                if is_5xx:
                    current_trace["http_5xx_count"] = int(current_trace["http_5xx_count"]) + 1
                if is_slow:
                    current_trace["slow_count"] = int(current_trace["slow_count"]) + 1
                if isinstance(cost_ms, int):
                    current_trace["max_cost_ms"] = max(int(current_trace["max_cost_ms"]), cost_ms)
                if uri:
                    current_trace["uris"].add(uri)
                if message and not current_trace["sample_message"]:
                    current_trace["sample_message"] = message[:300]

            if len(sample_evidence) < MAX_OVERVIEW_ITEMS and (level in {"ERROR", "WARN"} or is_5xx or is_slow):
                sample_evidence.append(
                    {
                        "time": time_text,
                        "level": level,
                        "traceId": trace_id or None,
                        "uri": uri or None,
                        "status": status,
                        "costMs": cost_ms,
                        "message": message[:300],
                        "file": str(log_path),
                    }
                )

    top_error_patterns = [
        {
            "level": level,
            "message": message,
            "count": int(data.get("count") or 0),
            "first_seen": data.get("first_seen"),
        }
        for (level, message), data in sorted(
            error_patterns.items(), key=lambda x: int(x[1].get("count") or 0), reverse=True
        )[:MAX_OVERVIEW_PATTERNS]
    ]

    top_affected_uris: List[Dict[str, object]] = []
    for item in uri_buckets.values():
        top_affected_uris.append(
            {
                "uri": item.get("uri"),
                "request_count": int(item.get("request_count") or 0),
                "error_count": int(item.get("error_count") or 0),
                "warn_count": int(item.get("warn_count") or 0),
                "http_5xx_count": int(item.get("http_5xx_count") or 0),
                "slow_count": int(item.get("slow_count") or 0),
                "max_cost_ms": int(item.get("max_cost_ms") or 0),
                "status_codes": dict(item.get("status_codes") or {}),
            }
        )
    top_affected_uris = sorted(
        top_affected_uris,
        key=lambda x: (
            int(x.get("http_5xx_count") or 0),
            int(x.get("slow_count") or 0),
            int(x.get("error_count") or 0),
            int(x.get("request_count") or 0),
        ),
        reverse=True,
    )[:MAX_OVERVIEW_ITEMS]

    top_suspicious_traces: List[Dict[str, object]] = []
    for item in trace_buckets.values():
        top_suspicious_traces.append(
            {
                "traceId": item.get("traceId"),
                "total_lines": int(item.get("total_lines") or 0),
                "error_count": int(item.get("error_count") or 0),
                "warn_count": int(item.get("warn_count") or 0),
                "http_5xx_count": int(item.get("http_5xx_count") or 0),
                "slow_count": int(item.get("slow_count") or 0),
                "max_cost_ms": int(item.get("max_cost_ms") or 0),
                "uris": sorted(list(item.get("uris") or []))[:5],
                "sample_message": item.get("sample_message") or "",
            }
        )
    top_suspicious_traces = sorted(
        top_suspicious_traces,
        key=lambda x: (
            int(x.get("error_count") or 0),
            int(x.get("http_5xx_count") or 0),
            int(x.get("slow_count") or 0),
            int(x.get("total_lines") or 0),
        ),
        reverse=True,
    )[:MAX_OVERVIEW_ITEMS]

    return json.dumps(
        {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "time_range": {"from": start.isoformat(), "to": end.isoformat()},
            "log_files": [str(p) for p in files],
            "summary": {
                "files_scanned": len(files),
                "lines_scanned": lines_scanned,
                "lines_in_window": lines_in_window,
                "error_count": error_count,
                "warn_count": warn_count,
                "http_5xx_count": http_5xx_count,
                "slow_request_count": slow_request_count,
                "affected_uri_count": len(uri_buckets),
                "trace_count": len(trace_buckets),
                "parse_error_count": parse_error_count,
            },
            "top_error_patterns": top_error_patterns,
            "top_affected_uris": top_affected_uris,
            "top_suspicious_traces": top_suspicious_traces,
            "sample_evidence": sample_evidence,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="analyze_log_around_alert",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
    description="Analyze logs around an alert time and summarize ERROR/WARN patterns.",
)
def analyze_log_around_alert(alert_time: str, window_minutes: int = 5) -> str:
    """
    围绕告警时间分析日志中的 ERROR/WARN 片段，输出结构化摘要。

    功能解释:
    - 自动根据 `alert_time` 选择日志文件。
    - 在给定时间窗口内统计 ERROR/WARN 事件。
    - 对重复报错做聚合，输出高频问题摘要。

    使用场景:
    - 告警发生后，先看附近窗口内日志发生了什么。
    - 分析报错集中点、是否存在大量解析失败日志。

    参数说明:
    - `alert_time` (str，必填)：告警时间，建议 ISO-8601。
    - `window_minutes` (int，可选，默认 `5`)：告警前后窗口，范围会限制在 `1 ~ 60`。

    必填字段:
    - `alert_time`

    调用方法:
    - `analyze_log_around_alert(alert_time="2026-05-11T10:00:00+08:00")`
    - `dispatch_tool(action="analyze_log_around_alert", params={"alert_time":"2026-05-11T10:00:00+08:00","window_minutes":10})`

    返回关键字段:
    - `analyzed_at`：分析时间。
    - `time_range`：实际分析窗口。
    - `log_files`：参与分析的日志文件列表。
    - `entries`：聚合后的 ERROR/WARN 摘要。
    - `error_count` / `warn_count` / `parse_error_count`：统计计数。
    """
    # 获取告警时间
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    # 计算时间窗口
    window_minutes = max(1, min(window_minutes, 60))
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    # 查找日志文件
    files, checked = _resolve_log_files(alert_dt, window_minutes)

    logger.info(f"analyze log around alert find log files: {files}")

    if not files:
        return json.dumps(
            {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "time_range": {"from": start.isoformat(), "to": end.isoformat()},
                "entries": [],
                "warning": "no log files found for alert window",
                "checked_files": checked,
            },
            ensure_ascii=False,
        )

    # 按 (level, message) 聚合计数
    buckets: Dict[Tuple[str, str], Dict[str, object]] = defaultdict(lambda: {"count": 0, "time": None})
    # 解析失败计数
    parse_error_count = 0

    # 遍历日志文件
    for log_path in files:
        # 读取日志文件最后几行
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        # 遍历每一行
        for line in lines:
            # 对每一行进行解析
            parsed_line = _parse_log_line(line, alert_dt)

            # 获取时间戳
            line_time = parsed_line.get("timestamp")
            # 不在时间窗口内
            if line_time and not (start <= line_time <= end):
                continue
            # 解析错误
            if parsed_line.get("parse_error") and (parsed_line.get("level") or LEVEL_PATTERN.search(line)):
                parse_error_count += 1

            # 获取日志级别
            level_raw = str(parsed_line.get("level") or "").upper()
            level = "WARN" if level_raw == "WARNING" else level_raw
            # 获取日志 message
            message = str(parsed_line.get("message") or _normalize_message(line)).strip()

            # 聚合 ERROR/WARN
            if level not in {"ERROR", "WARN"}:
                continue
            if not message:
                continue
            
            key = (level, message[:300])
            buckets[key]["count"] = int(buckets[key]["count"]) + 1
            # 记录首次出现时间
            if buckets[key]["time"] is None and isinstance(line_time, datetime):
                buckets[key]["time"] = line_time.astimezone(_target_timezone()).strftime("%H:%M:%S")

    # 生成 entries, 每项包含 time, level, message, count
    # 按 count 降序排序取前 MAX_OUTPUT_ENTRIES
    entries: List[Dict[str, object]] = []
    for (level, message), data in sorted(
        buckets.items(), key=lambda x: int(x[1]["count"]), reverse=True
    )[:MAX_OUTPUT_ENTRIES]:
        entries.append(
            {
                "time": data["time"] or alert_dt.strftime("%H:%M:%S"),
                "level": level,
                "message": message,
                "count": int(data["count"]),
            }
        )

    return json.dumps(
        {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "time_range": {"from": start.isoformat(), "to": end.isoformat()},
            "log_files": [str(p) for p in files],
            "entries": entries,
            "error_count": sum(int(item.get("count") or 0) for item in entries if item.get("level") == "ERROR"),
            "warn_count": sum(int(item.get("count") or 0) for item in entries if item.get("level") == "WARN"),
            "parse_error_count": parse_error_count,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="analyze_slow_requests",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
    description="Analyze slow requests around an alert time.",
)
def analyze_slow_requests(
    alert_time: str,
    window_minutes: int = 5,
    min_cost_ms: int = 1000,
    max_requests: int = 20,
) -> str:
    """
    分析告警时间窗口内的慢请求。

    功能解释:
    - 扫描告警时间窗口内日志。
    - 提取 `costMs >= min_cost_ms` 的请求日志。
    - 按耗时从高到低返回慢请求样例。

    使用场景:
    - 响应时间、延迟、超时类告警。

    参数说明:
    - `alert_time` (str，必填)：告警时间，建议 ISO-8601。
    - `window_minutes` (int，可选，默认 `5`)：告警前后窗口，范围 `1 ~ 60`。
    - `min_cost_ms` (int，可选，默认 `1000`)：慢请求阈值，范围 `1 ~ 600000`。
    - `max_requests` (int，可选，默认 `20`)：最多返回请求数，范围 `1 ~ 100`。

    返回关键字段:
    - `slow_request_count`：匹配慢请求总数。
    - `slow_requests`：按耗时降序排列的慢请求。
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    window_minutes = max(1, min(window_minutes, 60))
    min_cost_ms = max(1, min(int(min_cost_ms), 600000))
    max_requests = max(1, min(int(max_requests), 100))
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    files, checked = _resolve_log_files(alert_dt, window_minutes)
    if not files:
        return json.dumps(
            {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "time_range": {"from": start.isoformat(), "to": end.isoformat()},
                "slow_request_count": 0,
                "slow_requests": [],
                "warning": "no log files found for alert window",
                "checked_files": checked,
            },
            ensure_ascii=False,
        )

    slow_requests: List[Dict[str, object]] = []
    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        for line in lines:
            parsed_line = _parse_log_line(line, alert_dt)
            line_time = parsed_line.get("timestamp")
            if line_time and not (start <= line_time <= end):
                continue

            cost_ms = parsed_line.get("costMs")
            if not isinstance(cost_ms, int) or cost_ms < min_cost_ms:
                continue

            message = str(parsed_line.get("message") or _normalize_message(line)).strip()
            slow_requests.append(
                {
                    "time": line_time.isoformat() if isinstance(line_time, datetime) else alert_dt.isoformat(),
                    "traceId": parsed_line.get("traceId"),
                    "uri": parsed_line.get("uri"),
                    "status": parsed_line.get("status"),
                    "costMs": cost_ms,
                    "message": message[:300],
                    "file": str(log_path),
                }
            )

    sorted_requests = sorted(slow_requests, key=lambda x: int(x.get("costMs") or 0), reverse=True)
    return json.dumps(
        {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "time_range": {"from": start.isoformat(), "to": end.isoformat()},
            "log_files": [str(p) for p in files],
            "min_cost_ms": min_cost_ms,
            "slow_request_count": len(slow_requests),
            "slow_requests": sorted_requests[:max_requests],
        },
        ensure_ascii=False,
    )


@register_tool(
    name="analyze_error_requests",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
    description="Analyze HTTP error requests around an alert time.",
)
def analyze_error_requests(
    alert_time: str,
    window_minutes: int = 5,
    min_status: int = 500,
    max_requests: int = 20,
) -> str:
    """
    分析告警时间窗口内的 HTTP 错误请求。

    功能解释:
    - 扫描告警时间窗口内日志。
    - 提取 `status >= min_status` 的请求日志。
    - 返回错误请求样例和状态码分布。

    使用场景:
    - 错误率、HTTP 5xx、接口失败类告警。

    参数说明:
    - `alert_time` (str，必填)：告警时间，建议 ISO-8601。
    - `window_minutes` (int，可选，默认 `5`)：告警前后窗口，范围 `1 ~ 60`。
    - `min_status` (int，可选，默认 `500`)：错误状态码阈值，范围 `400 ~ 599`。
    - `max_requests` (int，可选，默认 `20`)：最多返回请求数，范围 `1 ~ 100`。

    返回关键字段:
    - `error_request_count`：匹配错误请求总数。
    - `status_codes`：状态码分布。
    - `error_requests`：错误请求样例。
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    window_minutes = max(1, min(window_minutes, 60))
    min_status = max(400, min(int(min_status), 599))
    max_requests = max(1, min(int(max_requests), 100))
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    files, checked = _resolve_log_files(alert_dt, window_minutes)
    if not files:
        return json.dumps(
            {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "time_range": {"from": start.isoformat(), "to": end.isoformat()},
                "error_request_count": 0,
                "status_codes": {},
                "error_requests": [],
                "warning": "no log files found for alert window",
                "checked_files": checked,
            },
            ensure_ascii=False,
        )

    status_codes: Dict[str, int] = defaultdict(int)
    error_requests: List[Dict[str, object]] = []
    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        for line in lines:
            parsed_line = _parse_log_line(line, alert_dt)
            line_time = parsed_line.get("timestamp")
            if line_time and not (start <= line_time <= end):
                continue

            status = parsed_line.get("status")
            if not isinstance(status, int) or status < min_status:
                continue

            status_codes[str(status)] += 1
            message = str(parsed_line.get("message") or _normalize_message(line)).strip()
            error_requests.append(
                {
                    "time": line_time.isoformat() if isinstance(line_time, datetime) else alert_dt.isoformat(),
                    "traceId": parsed_line.get("traceId"),
                    "uri": parsed_line.get("uri"),
                    "status": status,
                    "costMs": parsed_line.get("costMs"),
                    "message": message[:300],
                    "file": str(log_path),
                }
            )

    return json.dumps(
        {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "time_range": {"from": start.isoformat(), "to": end.isoformat()},
            "log_files": [str(p) for p in files],
            "min_status": min_status,
            "error_request_count": len(error_requests),
            "status_codes": dict(status_codes),
            "error_requests": error_requests[:max_requests],
        },
        ensure_ascii=False,
    )


@register_tool(
    name="aggregate_log_by_trace_id",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
    description="Aggregate logs by traceId within an alert time window.",
)
def aggregate_log_by_trace_id(alert_time: str, window_minutes: int = 5, max_traces: int = 20) -> str:
    """
    在告警时间窗口内按 traceId 聚合日志，输出每条请求链路的异常摘要。

    功能解释:
    - 自动扫描窗口内日志，按 `traceId` 归并同一请求链路日志。
    - 统计每个 trace 的 ERROR/WARN、慢请求、5xx、最大耗时和涉及 URI。
    - 便于快速定位“哪几条请求链路最异常”。

    参数说明:
    - `alert_time` (str，必填)：告警时间，建议 ISO-8601。
    - `window_minutes` (int，可选，默认 `5`)：告警前后窗口，范围 `1 ~ 60`。
    - `max_traces` (int，可选，默认 `20`)：最多返回多少条 trace 聚合结果，范围 `1 ~ 100`。

    返回关键字段:
    - `time_range`：实际分析窗口。
    - `log_files`：参与分析的日志文件。
    - `trace_count`：trace 聚合条数。
    - `traces`：trace 聚合列表。
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    window_minutes = max(1, min(window_minutes, 60))
    max_traces = max(1, min(int(max_traces), 100))
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    files, checked = _resolve_log_files(alert_dt, window_minutes)
    if not files:
        return json.dumps(
            {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "time_range": {"from": start.isoformat(), "to": end.isoformat()},
                "warning": "no log files found for alert window",
                "checked_files": checked,
                "traces": [],
            },
            ensure_ascii=False,
        )

    buckets: Dict[str, Dict[str, object]] = {}
    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        for line in lines:
            parsed_line = _parse_log_line(line, alert_dt)
            line_time = parsed_line.get("timestamp")
            if line_time and not (start <= line_time <= end):
                continue

            trace_id = str(parsed_line.get("traceId") or "").strip()
            if not trace_id:
                continue

            current = buckets.get(trace_id)
            if current is None:
                current = {
                    "traceId": trace_id,
                    "total_lines": 0,
                    "error_count": 0,
                    "warn_count": 0,
                    "slow_count": 0,
                    "http_5xx_count": 0,
                    "max_cost_ms": 0,
                    "uris": set(),
                    "files": set(),
                    "first_seen": None,
                    "last_seen": None,
                    "sample_messages": [],
                }
                buckets[trace_id] = current

            current["total_lines"] = int(current["total_lines"]) + 1
            current["files"].add(str(log_path))

            if isinstance(line_time, datetime):
                first_seen = current["first_seen"]
                last_seen = current["last_seen"]
                if not isinstance(first_seen, datetime) or line_time < first_seen:
                    current["first_seen"] = line_time
                if not isinstance(last_seen, datetime) or line_time > last_seen:
                    current["last_seen"] = line_time

            level_raw = str(parsed_line.get("level") or "").upper()
            level = "WARN" if level_raw == "WARNING" else level_raw
            if level == "ERROR":
                current["error_count"] = int(current["error_count"]) + 1
            elif level == "WARN":
                current["warn_count"] = int(current["warn_count"]) + 1

            cost_ms = parsed_line.get("costMs")
            if isinstance(cost_ms, int) and cost_ms >= 1000:
                current["slow_count"] = int(current["slow_count"]) + 1
            if isinstance(cost_ms, int):
                current["max_cost_ms"] = max(int(current["max_cost_ms"]), cost_ms)

            status = parsed_line.get("status")
            if isinstance(status, int) and status >= 500:
                current["http_5xx_count"] = int(current["http_5xx_count"]) + 1

            uri = str(parsed_line.get("uri") or "").strip()
            if uri:
                current["uris"].add(uri)

            msg = str(parsed_line.get("message") or _normalize_message(line)).strip()
            if msg and len(current["sample_messages"]) < 3:
                current["sample_messages"].append(msg[:300])

    traces: List[Dict[str, object]] = []
    for _, item in buckets.items():
        first_seen = item.get("first_seen")
        last_seen = item.get("last_seen")
        traces.append(
            {
                "traceId": item.get("traceId"),
                "total_lines": int(item.get("total_lines") or 0),
                "error_count": int(item.get("error_count") or 0),
                "warn_count": int(item.get("warn_count") or 0),
                "slow_count": int(item.get("slow_count") or 0),
                "http_5xx_count": int(item.get("http_5xx_count") or 0),
                "max_cost_ms": int(item.get("max_cost_ms") or 0),
                "uri_count": len(item.get("uris") or []),
                "uris": sorted(list(item.get("uris") or []))[:10],
                "file_count": len(item.get("files") or []),
                "files": sorted(list(item.get("files") or []))[:5],
                "first_seen": first_seen.isoformat() if isinstance(first_seen, datetime) else None,
                "last_seen": last_seen.isoformat() if isinstance(last_seen, datetime) else None,
                "sample_messages": item.get("sample_messages") or [],
            }
        )

    traces = sorted(
        traces,
        key=lambda x: (
            int(x.get("error_count") or 0),
            int(x.get("http_5xx_count") or 0),
            int(x.get("slow_count") or 0),
            int(x.get("total_lines") or 0),
        ),
        reverse=True,
    )[:max_traces]

    return json.dumps(
        {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "time_range": {"from": start.isoformat(), "to": end.isoformat()},
            "log_files": [str(p) for p in files],
            "trace_count": len(traces),
            "traces": traces,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="aggregate_log_by_uri",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
    description="Aggregate logs by URI within an alert time window.",
)
def aggregate_log_by_uri(alert_time: str, window_minutes: int = 5, max_uris: int = 20) -> str:
    """
    在告警时间窗口内按 URI/接口聚合日志，输出接口级异常摘要。

    功能解释:
    - 自动扫描窗口内日志，按 `uri` 聚合请求。
    - 统计每个 URI 的请求量、ERROR/WARN、5xx、慢请求和耗时指标。
    - 便于快速定位“哪些接口受影响最明显”。

    参数说明:
    - `alert_time` (str，必填)：告警时间，建议 ISO-8601。
    - `window_minutes` (int，可选，默认 `5`)：告警前后窗口，范围 `1 ~ 60`。
    - `max_uris` (int，可选，默认 `20`)：最多返回多少个 URI 聚合结果，范围 `1 ~ 100`。

    返回关键字段:
    - `time_range`：实际分析窗口。
    - `log_files`：参与分析的日志文件。
    - `uri_count`：URI 聚合条数。
    - `uris`：URI 聚合列表。
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    window_minutes = max(1, min(window_minutes, 60))
    max_uris = max(1, min(int(max_uris), 100))
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    files, checked = _resolve_log_files(alert_dt, window_minutes)
    if not files:
        return json.dumps(
            {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "time_range": {"from": start.isoformat(), "to": end.isoformat()},
                "warning": "no log files found for alert window",
                "checked_files": checked,
                "uris": [],
            },
            ensure_ascii=False,
        )

    buckets: Dict[str, Dict[str, object]] = {}
    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        for line in lines:
            parsed_line = _parse_log_line(line, alert_dt)
            line_time = parsed_line.get("timestamp")
            if line_time and not (start <= line_time <= end):
                continue

            uri = str(parsed_line.get("uri") or "").strip()
            if not uri:
                continue

            current = buckets.get(uri)
            if current is None:
                current = {
                    "uri": uri,
                    "request_count": 0,
                    "error_count": 0,
                    "warn_count": 0,
                    "slow_count": 0,
                    "http_5xx_count": 0,
                    "max_cost_ms": 0,
                    "total_cost_ms": 0,
                    "cost_samples": 0,
                    "status_codes": defaultdict(int),
                }
                buckets[uri] = current

            current["request_count"] = int(current["request_count"]) + 1

            level_raw = str(parsed_line.get("level") or "").upper()
            level = "WARN" if level_raw == "WARNING" else level_raw
            if level == "ERROR":
                current["error_count"] = int(current["error_count"]) + 1
            elif level == "WARN":
                current["warn_count"] = int(current["warn_count"]) + 1

            cost_ms = parsed_line.get("costMs")
            if isinstance(cost_ms, int):
                current["max_cost_ms"] = max(int(current["max_cost_ms"]), cost_ms)
                current["total_cost_ms"] = int(current["total_cost_ms"]) + cost_ms
                current["cost_samples"] = int(current["cost_samples"]) + 1
                if cost_ms >= 1000:
                    current["slow_count"] = int(current["slow_count"]) + 1

            status = parsed_line.get("status")
            if isinstance(status, int):
                current["status_codes"][str(status)] += 1
                if status >= 500:
                    current["http_5xx_count"] = int(current["http_5xx_count"]) + 1

    uris: List[Dict[str, object]] = []
    for _, item in buckets.items():
        cost_samples = int(item.get("cost_samples") or 0)
        total_cost = int(item.get("total_cost_ms") or 0)
        avg_cost_ms = round(total_cost / cost_samples, 2) if cost_samples > 0 else None
        uris.append(
            {
                "uri": item.get("uri"),
                "request_count": int(item.get("request_count") or 0),
                "error_count": int(item.get("error_count") or 0),
                "warn_count": int(item.get("warn_count") or 0),
                "slow_count": int(item.get("slow_count") or 0),
                "http_5xx_count": int(item.get("http_5xx_count") or 0),
                "max_cost_ms": int(item.get("max_cost_ms") or 0),
                "avg_cost_ms": avg_cost_ms,
                "status_codes": dict(item.get("status_codes") or {}),
            }
        )

    uris = sorted(
        uris,
        key=lambda x: (
            int(x.get("http_5xx_count") or 0),
            int(x.get("slow_count") or 0),
            int(x.get("error_count") or 0),
            int(x.get("request_count") or 0),
        ),
        reverse=True,
    )[:max_uris]

    return json.dumps(
        {
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "time_range": {"from": start.isoformat(), "to": end.isoformat()},
            "log_files": [str(p) for p in files],
            "uri_count": len(uris),
            "uris": uris,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="retrieve_log_context",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
    description="Search logs for a pattern and return matching lines with context.",
)
def retrieve_log_context(
    pattern: str,
    alert_time: str,
    window_minutes: int = 5,
    context_lines: int = 3,
    max_matches: int = 3,
    pattern_type: str = "literal",
    case_sensitive: bool = False,
) -> str:
    """
    在日志中检索 `pattern`，并返回命中行前后上下文的结构化结果。

    功能解释:
    - 支持 literal 和 regex 两种模式。
    - 自动根据 `alert_time` 选择对应时间窗口内的日志文件。
    - 返回命中行、上下文行、文件路径和匹配元信息。

    使用场景:
    - 对聚合分析中的高频错误做原文复核。
    - 搜索特定 traceId、错误片段、URI、异常关键词。

    参数说明:
    - `pattern` (str，必填)：匹配模式，文本或正则。
    - `alert_time` (str，必填)：告警时间，建议 ISO-8601。
    - `window_minutes` (int，可选，默认 `5`)：时间窗口，范围 `1 ~ 60`。
    - `context_lines` (int，可选，默认 `3`)：命中前后上下文行数，范围 `0 ~ 20`。
    - `max_matches` (int，可选，默认 `3`)：最多返回多少个命中，范围 `1 ~ 10`。
    - `pattern_type` (str，可选，默认 `literal`)：`literal` 或 `regex`。
    - `case_sensitive` (bool，可选，默认 `False`)：是否区分大小写。

    必填字段:
    - `pattern`
    - `alert_time`

    调用方法:
    - `retrieve_log_context(pattern="traceId=xxx", alert_time="2026-05-11T10:00:00+08:00")`
    - `retrieve_log_context(pattern="NullPointerException", alert_time="2026-05-11T10:00:00+08:00", pattern_type="literal", case_sensitive=False)`

    返回关键字段:
    - `analyzed_at`：分析时间。
    - `time_range`：实际检索窗口。
    - `log_files`：参与检索的日志文件。
    - `pattern` / `pattern_type` / `case_sensitive`：检索条件回显。
    - `context_lines` / `max_matches`：上下文配置。
    - `matches_found`：命中数。
    - `matches`：命中列表，每项含文件、命中行号、命中内容和上下文。
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    window_minutes = max(1, min(window_minutes, 60))
    context_lines = max(0, min(int(context_lines), MAX_CONTEXT_LINES))
    max_matches = max(1, min(int(max_matches), MAX_CONTEXT_MATCHES))

    # 计算时间窗口
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    match_type = (pattern_type or "literal").lower()
    if match_type not in ("literal", "regex"):
        return json.dumps({"error": "pattern_type must be 'literal' or 'regex'"}, ensure_ascii=False)

    # 查找日志文件
    files, checked = _resolve_log_files(alert_dt, window_minutes)
    if not files:
        return json.dumps(
            {
                "error": "no log files found for alert window",
                "checked_files": checked,
            },
            ensure_ascii=False,
        )

    # 正则匹配
    if match_type == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(pattern, flags)
        except re.error as e:
            return json.dumps({"error": f"invalid regex: {e}"}, ensure_ascii=False)

        def is_match_fn(text: str) -> bool:
            return matcher.search(text) is not None
    else:
        # 子串匹配
        needle = pattern if case_sensitive else pattern.lower()

        def is_match_fn(text: str) -> bool:
            hay = text if case_sensitive else text.lower()
            return needle in hay

    matches: List[Dict[str, object]] = []

    # 遍历日志文件
    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        for idx, line in enumerate(lines):
            line_time = _parse_line_timestamp(line, alert_dt)
            if line_time and not (start <= line_time <= end):
                continue
            if not is_match_fn(line):
                continue

            # 生成上下文片段
            start_idx = max(0, idx - context_lines)
            end_idx = min(len(lines), idx + context_lines + 1)
            snippet: List[Dict[str, object]] = []
            for i in range(start_idx, end_idx):
                snippet.append(
                    {
                        "line_no": i + 1,
                        "text": lines[i][:500],
                        "is_match": i == idx,
                    }
                )

            matches.append(
                {
                    "file": str(log_path),
                    "match_line_no": idx + 1,
                    "match_text": line[:500],
                    "context": snippet,
                }
            )
            if len(matches) >= max_matches:
                break
        if len(matches) >= max_matches:
            break

    result = {
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "time_range": {"from": start.isoformat(), "to": end.isoformat()},
        "log_files": [str(p) for p in files],
        "pattern": pattern,
        "pattern_type": match_type,
        "case_sensitive": bool(case_sensitive),
        "context_lines": context_lines,
        "max_matches": max_matches,
        "matches_found": len(matches),
        "matches": matches,
    }
    return json.dumps(result, ensure_ascii=False)[:MAX_CONTEXT_OUTPUT_CHARS]


@register_tool(
    name="retrieve_log_context_raw",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
    description="Search logs for a pattern and return raw matched context text.",
)
def retrieve_log_context_raw(
    pattern: str,
    alert_time: str,
    window_minutes: int = 5,
    context_lines: int = 3,
    max_matches: int = 3,
    pattern_type: str = "literal",
    case_sensitive: bool = False,
) -> str:
    """
    返回日志命中片段的原始文本，不做 JSON 结构化包装。

    功能解释:
    - 与 `retrieve_log_context` 的检索逻辑一致。
    - 输出为纯文本片段，适合人工直接阅读或复制到其他系统。

    使用场景:
    - 需要直接查看原始日志段落。
    - 需要更接近原文格式的上下文。

    参数说明:
    - `pattern` (str，必填)：匹配模式。
    - `alert_time` (str，必填)：告警时间。
    - `window_minutes` (int，可选，默认 `5`)：时间窗口。
    - `context_lines` (int，可选，默认 `3`)：上下文行数。
    - `max_matches` (int，可选，默认 `3`)：最多返回命中数。
    - `pattern_type` (str，可选，默认 `literal`)：`literal` 或 `regex`。
    - `case_sensitive` (bool，可选，默认 `False`)：是否区分大小写。

    必填字段:
    - `pattern`
    - `alert_time`

    调用方法:
    - `retrieve_log_context_raw(pattern="traceId=xxx", alert_time="2026-05-11T10:00:00+08:00")`

    返回关键字段:
    - 直接返回纯文本内容，不做 JSON 包装。
    - 无命中时返回简短错误或提示字符串。
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return f"Error: invalid alert_time: {alert_time}"

    window_minutes = max(1, min(window_minutes, 60))
    context_lines = max(0, min(int(context_lines), MAX_CONTEXT_LINES))
    max_matches = max(1, min(int(max_matches), MAX_CONTEXT_MATCHES))

    match_type = (pattern_type or "literal").lower()
    if match_type not in ("literal", "regex"):
        return "Error: pattern_type must be 'literal' or 'regex'"

    files, checked = _resolve_log_files(alert_dt, window_minutes)
    if not files:
        return f"Error: no log files found for alert window. checked={checked}"

    if match_type == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(pattern, flags)
        except re.error as e:
            return f"Error: invalid regex: {e}"

        def is_match_fn(text: str) -> bool:
            return matcher.search(text) is not None
    else:
        needle = pattern if case_sensitive else pattern.lower()

        def is_match_fn(text: str) -> bool:
            hay = text if case_sensitive else text.lower()
            return needle in hay

    chunks: List[str] = []
    remaining = max_matches

    for path in files:
        if remaining <= 0:
            break
        lines = _read_tail_lines(path, MAX_SCAN_LINES)
        file_chunks: List[str] = []
        for idx, line in enumerate(lines):
            if not is_match_fn(line):
                continue
            start_idx = max(0, idx - context_lines)
            end_idx = min(len(lines), idx + context_lines + 1)
            snippet = []
            for i in range(start_idx, end_idx):
                prefix = f"{i + 1}:"
                snippet.append(prefix + lines[i])
            file_chunks.append("\n".join(snippet))
            remaining -= 1
            if remaining <= 0:
                break
        if file_chunks:
            chunks.append(f"# {path}\n" + "\n--\n".join(file_chunks))

    if not chunks:
        return f"No matches found for '{pattern}'."

    return "\n\n".join(chunks)[:MAX_CONTEXT_OUTPUT_CHARS]
