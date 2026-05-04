from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.db.session import AsyncSessionLocal
from app.model.audit_log import AuditLog
from app.model.user import User


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


def audit_log_from_entry(entry: dict[str, Any]) -> AuditLog:
    event_type = str(entry.get("event") or "unknown")
    return AuditLog(
        timestamp=_parse_timestamp(entry.get("timestamp")) or datetime.utcnow(),
        user_id=str(entry.get("user_id") or "unknown"),
        user_role=str(entry.get("user_role") or "unknown"),
        event_type=event_type,
        tool_name=str(entry.get("tool")) if entry.get("tool") else None,
        tool_permission=str(entry.get("tool_permission")) if entry.get("tool_permission") else None,
        details=entry,
        status=str(entry.get("status")) if entry.get("status") else _status_from_event(event_type),
        error_message=str(entry.get("error")) if entry.get("error") else None,
    )


def _request_meta(request: Request | None) -> dict[str, str | None]:
    if request is None:
        return {"ip_address": None, "user_agent": None}
    return {
        "ip_address": request.client.host if request.client else None,
        "user_agent": request.headers.get("user-agent"),
    }


async def write_audit_log(
    db: AsyncSession,
    *,
    current_user: User | None = None,
    user_id: str | int | None = None,
    user_role: str | None = None,
    request: Request | None = None,
    event_type: str,
    status: str | None = None,
    tool_name: str | None = None,
    tool_permission: str | None = None,
    details: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    """Write one audit record without interrupting the caller on failure."""
    meta = _request_meta(request)
    resolved_user_id = user_id if user_id is not None else current_user.id if current_user else "unknown"
    resolved_user_role = user_role if user_role is not None else current_user.role if current_user else "unknown"
    try:
        db.add(
            AuditLog(
                user_id=str(resolved_user_id),
                user_role=str(resolved_user_role),
                event_type=event_type,
                status=status,
                tool_name=tool_name,
                tool_permission=tool_permission,
                ip_address=meta["ip_address"],
                user_agent=meta["user_agent"],
                details=details,
                error_message=error_message,
            )
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error("write audit log failed: %s", exc, exc_info=True)


async def write_system_audit_log(
    *,
    user_id: str | int = "system",
    user_role: str = "system",
    event_type: str,
    status: str | None = None,
    tool_name: str | None = None,
    tool_permission: str | None = None,
    details: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        await write_audit_log(
            db,
            user_id=user_id,
            user_role=user_role,
            event_type=event_type,
            status=status,
            tool_name=tool_name,
            tool_permission=tool_permission,
            details=details,
            error_message=error_message,
        )
