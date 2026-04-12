import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from app.agent.tools.security import register_tool

MAX_SCAN_LINES = 5000
MAX_OUTPUT_ENTRIES = 20

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


@register_tool(
    name="analyze_log_around_alert",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["log"],
)
def analyze_log_around_alert(log_file: str, alert_time: str, window_minutes: int = 5) -> str:
    """
    Analyze ERROR/WARN logs around alert time and return structured summary JSON.
    """
    alert_dt = _parse_iso_datetime(alert_time)
    if not alert_dt:
        return json.dumps({"error": f"invalid alert_time: {alert_time}"}, ensure_ascii=False)

    window_minutes = max(1, min(window_minutes, 60))
    start = alert_dt - timedelta(minutes=window_minutes)
    end = alert_dt + timedelta(minutes=window_minutes)

    path = Path(log_file)
    if not path.exists() or not path.is_file():
        return json.dumps(
            {
                "analyzed_at": datetime.now(timezone.utc).isoformat(),
                "time_range": {"from": start.isoformat(), "to": end.isoformat()},
                "entries": [],
                "warning": f"log file not found: {log_file}",
            },
            ensure_ascii=False,
        )

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception as e:
        return json.dumps({"error": f"failed to read log file: {e}"}, ensure_ascii=False)

    if len(lines) > MAX_SCAN_LINES:
        lines = lines[-MAX_SCAN_LINES:]

    buckets: Dict[Tuple[str, str], Dict[str, object]] = defaultdict(lambda: {"count": 0, "time": None})

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
            "entries": entries,
        },
        ensure_ascii=False,
    )
