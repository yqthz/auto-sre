import json
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.agent.tools.security import register_tool

MAX_SCAN_LINES = 5000
MAX_OUTPUT_ENTRIES = 20
MAX_CONTEXT_LINES = 20
MAX_CONTEXT_MATCHES = 10
MAX_CONTEXT_OUTPUT_CHARS = 8000

# Fixed production log location and naming rule (daily rotated).
LOG_DIR = Path("/var/log/auto-sre")
LOG_FILE_PATTERN = "app-{date}.log"  # {date} => YYYY-MM-DD

TIMESTAMP_PATTERNS = [
    re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"),
    re.compile(r"(?P<ts>\d{2}:\d{2}:\d{2})"),
]
LEVEL_PATTERN = re.compile(r"\b(ERROR|WARN|WARNING)\b", re.IGNORECASE)


def _parse_iso_datetime(value: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
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
                return datetime.combine(base_date.date(), t, tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            parsed = datetime.fromisoformat(raw.replace(" ", "T"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def _normalize_message(line: str) -> str:
    msg = line.strip()
    # Remove leading timestamp and log level noise to improve aggregation.
    msg = re.sub(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*", "", msg)
    msg = re.sub(r"^\d{2}:\d{2}:\d{2}\s*", "", msg)
    msg = re.sub(r"\b(ERROR|WARN|WARNING)\b[:\s-]*", "", msg, flags=re.IGNORECASE)
    msg = re.sub(r"\s+", " ", msg).strip()
    return msg[:200]


def _resolve_log_files(alert_dt: datetime, window_minutes: int) -> Tuple[List[Path], List[str]]:
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    candidate_dates = set()
    cursor = start.date()
    while cursor <= end.date():
        candidate_dates.add(cursor)
        cursor = cursor + timedelta(days=1)

    # Fallback: always include previous/next day around alert date.
    candidate_dates.add(alert_dt.date() - timedelta(days=1))
    candidate_dates.add(alert_dt.date() + timedelta(days=1))

    checked: List[str] = []
    existing: List[Path] = []
    for day in sorted(candidate_dates):
        file_name = LOG_FILE_PATTERN.format(date=day.isoformat())
        p = LOG_DIR / file_name
        checked.append(str(p))
        if p.exists() and p.is_file():
            existing.append(p)
    return existing, checked


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

    for log_path in files:
        lines = _read_tail_lines(log_path, MAX_SCAN_LINES)
        for line in lines:
            level_match = LEVEL_PATTERN.search(line)
            if not level_match:
                continue
            level_raw = level_match.group(1).upper()
            level = "WARN" if level_raw == "WARNING" else level_raw

            line_time = _parse_line_timestamp(line, alert_dt)
            if line_time and not (start <= line_time <= end):
                continue

            message = _normalize_message(line)
            if not message:
                continue

            key = (level, message)
            buckets[key]["count"] = int(buckets[key]["count"]) + 1
            if buckets[key]["time"] is None and line_time is not None:
                buckets[key]["time"] = line_time.strftime("%H:%M:%S")

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
    使用 grep -n -C 回捞日志原始上下文片段。
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

    chunks: List[str] = []
    remaining = max_matches

    for path in files:
        if remaining <= 0:
            break

        cmd = ["grep", "-n", "-C", str(context_lines), "-m", str(remaining)]
        if match_type == "regex":
            cmd.append("-E")
        else:
            cmd.append("-F")
        if not case_sensitive:
            cmd.append("-i")
        cmd.extend(["--", pattern, str(path)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
            )
        except Exception as e:
            return f"Error running grep: {e}"

        if proc.returncode not in (0, 1):
            err = (proc.stderr or "grep failed").strip()
            return f"Grep failed: {err}"

        out = (proc.stdout or "").strip()
        if out:
            chunks.append(f"# {path}\n{out}")
            remaining -= out.count("\n--") + 1

    if not chunks:
        return f"No matches found for '{pattern}'."

    return "\n\n".join(chunks)[:MAX_CONTEXT_OUTPUT_CHARS]
