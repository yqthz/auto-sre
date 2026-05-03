import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional

from app.core.logger import logger
from app.db.session import AsyncSessionLocal
from app.model.audit_log import AuditLog


def _parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _status_from_event(event_type: str) -> Optional[str]:
    if event_type == "tool_call_request":
        return "requested"
    if event_type == "tool_call_result":
        return "success"
    if event_type == "tool_call_denied":
        return "denied"
    return None


async def _append_audit_async(entry: Dict):
    event_type = str(entry.get("event") or "unknown")
    audit = AuditLog(
        timestamp=_parse_timestamp(entry.get("timestamp")) or datetime.utcnow(),
        user_id=str(entry.get("user_id") or "unknown"),
        user_role=str(entry.get("user_role") or "unknown"),
        event_type=event_type,
        tool_name=str(entry.get("tool")) if entry.get("tool") else None,
        details=entry,
        status=str(entry.get("status")) if entry.get("status") else _status_from_event(event_type),
    )

    async with AsyncSessionLocal() as db:
        db.add(audit)
        await db.commit()


def append_audit(entry: Dict):
    """Persist one audit record into database."""

    async def _run():
        try:
            await _append_audit_async(entry)
        except Exception as exc:
            logger.error("append_audit db write failed: %s", exc, exc_info=True)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_run())
        return

    loop.create_task(_run())
