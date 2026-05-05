import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.agent.runtime_profile import get_runtime_profile
from app.agent.tools.security import register_tool

MAX_SCAN_LINES = 20000
MAX_OUTPUT_ENTRIES = 20
MAX_CONTEXT_LINES = 20
MAX_CONTEXT_MATCHES = 10
MAX_CONTEXT_OUTPUT_CHARS = 8000

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


def _parse_newbee_line(line: str, base_date: datetime) -> Dict[str, object]:
    match = LOG_PATTERN.match(line.rstrip("\n"))
    if not match:
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


def _resolve_log_files(alert_dt: datetime, window_minutes: int) -> Tuple[List[Path], List[str]]:
    profile = get_runtime_profile()
    log_dir = profile.app.log_dir
    patterns = profile.app.log_patterns
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

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
    name="analyze_log_around_alert",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
)
def analyze_log_around_alert(alert_time: str, window_minutes: int = 5) -> str:
    """
    Analyze ERROR/WARN logs around alert time and return structured summary JSON.
    Logs are selected automatically from fixed daily log files.
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
                "entries": [],
                "warning": "no log files found for alert window",
                "checked_files": checked,
            },
            ensure_ascii=False,
        )

    buckets: Dict[Tuple[str, str], Dict[str, object]] = defaultdict(lambda: {"count": 0, "time": None})
    slow_requests: List[Dict[str, object]] = []
    error_requests: List[Dict[str, object]] = []
    parse_error_count = 0

    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        for line in lines:
            parsed_line = _parse_newbee_line(line, alert_dt)

            line_time = parsed_line.get("timestamp")
            if line_time and not (start <= line_time <= end):
                continue
            if parsed_line.get("parse_error") and (parsed_line.get("level") or LEVEL_PATTERN.search(line)):
                parse_error_count += 1

            level_raw = str(parsed_line.get("level") or "").upper()
            level = "WARN" if level_raw == "WARNING" else level_raw
            message = str(parsed_line.get("message") or _normalize_message(line)).strip()

            status = parsed_line.get("status")
            cost_ms = parsed_line.get("costMs")
            uri = parsed_line.get("uri")

            if isinstance(cost_ms, int) and cost_ms >= 1000:
                slow_requests.append(
                    {
                        "time": line_time.isoformat() if isinstance(line_time, datetime) else alert_dt.isoformat(),
                        "traceId": parsed_line.get("traceId"),
                        "uri": uri,
                        "status": status,
                        "costMs": cost_ms,
                        "message": message[:300],
                        "file": str(log_path),
                    }
                )

            if isinstance(status, int) and status >= 500:
                error_requests.append(
                    {
                        "time": line_time.isoformat() if isinstance(line_time, datetime) else alert_dt.isoformat(),
                        "traceId": parsed_line.get("traceId"),
                        "uri": uri,
                        "status": status,
                        "costMs": cost_ms,
                        "message": message[:300],
                        "file": str(log_path),
                    }
                )

            if level not in {"ERROR", "WARN"}:
                continue
            if not message:
                continue

            key = (level, message[:300])
            buckets[key]["count"] = int(buckets[key]["count"]) + 1
            if buckets[key]["time"] is None and isinstance(line_time, datetime):
                buckets[key]["time"] = line_time.astimezone(_target_timezone()).strftime("%H:%M:%S")

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
            "slow_requests": sorted(slow_requests, key=lambda x: int(x.get("costMs") or 0), reverse=True)[:20],
            "error_requests": error_requests[:20],
            "error_count": sum(int(item.get("count") or 0) for item in entries if item.get("level") == "ERROR"),
            "warn_count": sum(int(item.get("count") or 0) for item in entries if item.get("level") == "WARN"),
            "parse_error_count": parse_error_count,
        },
        ensure_ascii=False,
    )


@register_tool(
    name="retrieve_log_context",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
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
    回捞日志匹配 pattern 的上下文（前后 N 行），用于对聚合结果做原文复核。
    pattern_type 支持 literal/regex。日志文件按 alert_time 自动选择。
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    window_minutes = max(1, min(window_minutes, 60))
    context_lines = max(0, min(int(context_lines), MAX_CONTEXT_LINES))
    max_matches = max(1, min(int(max_matches), MAX_CONTEXT_MATCHES))

    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    match_type = (pattern_type or "literal").lower()
    if match_type not in ("literal", "regex"):
        return json.dumps({"error": "pattern_type must be 'literal' or 'regex'"}, ensure_ascii=False)

    files, checked = _resolve_log_files(alert_dt, window_minutes)
    if not files:
        return json.dumps(
            {
                "error": "no log files found for alert window",
                "checked_files": checked,
            },
            ensure_ascii=False,
        )

    if match_type == "regex":
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(pattern, flags)
        except re.error as e:
            return json.dumps({"error": f"invalid regex: {e}"}, ensure_ascii=False)

        def is_match_fn(text: str) -> bool:
            return matcher.search(text) is not None
    else:
        needle = pattern if case_sensitive else pattern.lower()

        def is_match_fn(text: str) -> bool:
            hay = text if case_sensitive else text.lower()
            return needle in hay

    matches: List[Dict[str, object]] = []

    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        for idx, line in enumerate(lines):
            line_time = _parse_line_timestamp(line, alert_dt)
            if line_time and not (start <= line_time <= end):
                continue
            if not is_match_fn(line):
                continue

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
    回捞日志原始上下文片段。
    pattern_type 支持 literal/regex。日志文件按 alert_time 自动选择。
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
