import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core.logger import logger
from app.model.audit_log import AuditLog
from app.model.user import User
from app.schema.audit_log import AuditLogListResponse, AuditLogResponse

router = APIRouter()

_TOOL_EVENTS = {"tool_call_request", "tool_call_result", "tool_call_denied"}
_DEFAULT_QUERY_DAYS = 7
_MAX_DETAILS_CHARS = 16 * 1024


def _to_response(item: AuditLog, include_details: bool) -> AuditLogResponse:
    if include_details:
        return AuditLogResponse.model_validate(item)

    return AuditLogResponse(
        id=item.id,
        timestamp=item.timestamp,
        event_type=item.event_type,
        status=item.status,
        user_id=item.user_id,
        user_role=item.user_role,
        tool_name=item.tool_name,
        tool_permission=item.tool_permission,
        ip_address=item.ip_address,
        user_agent=item.user_agent,
        error_message=item.error_message,
        details=None,
    )


def _apply_role_scope(query, current_user: User):
    if current_user.role == "admin":
        return query

    if current_user.role == "viewer":
        return query.where(AuditLog.user_id == str(current_user.id))

    if current_user.role == "sre":
        return query.where(
            or_(
                AuditLog.event_type.in_(_TOOL_EVENTS),
                AuditLog.tool_name.is_not(None),
            )
        )

    raise HTTPException(status_code=403, detail="Current role is not allowed to access audit logs")


def _clip_details(details: dict) -> dict:
    raw = json.dumps(details, ensure_ascii=False)
    if len(raw) <= _MAX_DETAILS_CHARS:
        return details

    clipped_text = raw[:_MAX_DETAILS_CHARS]
    return {
        "truncated": True,
        "original_size": len(raw),
        "clipped_json": clipped_text,
    }


async def _write_audit_view(
    db: AsyncSession,
    request: Request,
    current_user: User,
    details: dict,
):
    try:
        db.add(
            AuditLog(
                user_id=str(current_user.id),
                user_role=current_user.role,
                event_type="audit_log_view",
                status="success",
                ip_address=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"),
                details=_clip_details(details),
            )
        )
        await db.commit()
    except Exception as exc:
        logger.error("write audit_log_view failed: %s", exc, exc_info=True)


@router.get("", response_model=AuditLogListResponse)
async def list_audit_logs(
    request: Request,
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    start_time: datetime | None = Query(None, description="开始时间（ISO8601）"),
    end_time: datetime | None = Query(None, description="结束时间（ISO8601）"),
    event_type: str | None = Query(None, description="事件类型"),
    status: str | None = Query(None, description="状态"),
    user_id: str | None = Query(None, description="用户ID"),
    tool_name: str | None = Query(None, description="工具名"),
    keyword: str | None = Query(None, description="关键词（事件/工具/错误）"),
    include_details: bool = Query(False, description="列表是否返回 details"),
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    """审计日志列表（第六步：默认时间窗口 + details 截断）"""
    effective_start_time = start_time
    if start_time is None and end_time is None:
        effective_start_time = datetime.utcnow() - timedelta(days=_DEFAULT_QUERY_DAYS)

    query = _apply_role_scope(select(AuditLog), current_user)
    count_query = _apply_role_scope(select(func.count(AuditLog.id)), current_user)

    if effective_start_time is not None:
        query = query.where(AuditLog.timestamp >= effective_start_time)
        count_query = count_query.where(AuditLog.timestamp >= effective_start_time)

    if end_time is not None:
        query = query.where(AuditLog.timestamp <= end_time)
        count_query = count_query.where(AuditLog.timestamp <= end_time)

    if event_type:
        query = query.where(AuditLog.event_type == event_type)
        count_query = count_query.where(AuditLog.event_type == event_type)

    if status:
        query = query.where(AuditLog.status == status)
        count_query = count_query.where(AuditLog.status == status)

    if user_id:
        query = query.where(AuditLog.user_id == user_id)
        count_query = count_query.where(AuditLog.user_id == user_id)

    if tool_name:
        query = query.where(AuditLog.tool_name == tool_name)
        count_query = count_query.where(AuditLog.tool_name == tool_name)

    if keyword:
        pattern = f"%{keyword}%"
        condition = or_(
            AuditLog.event_type.ilike(pattern),
            AuditLog.tool_name.ilike(pattern),
            AuditLog.error_message.ilike(pattern),
            AuditLog.user_id.ilike(pattern),
        )
        query = query.where(condition)
        count_query = count_query.where(condition)

    total_result = await db.execute(count_query)
    total = int(total_result.scalar() or 0)

    offset = (page - 1) * page_size
    query = (
        query
        .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .offset(offset)
        .limit(page_size)
    )
    result = await db.execute(query)
    logs = result.scalars().all()

    await _write_audit_view(
        db=db,
        request=request,
        current_user=current_user,
        details={
            "action": "list",
            "page": page,
            "page_size": page_size,
            "result_count": len(logs),
            "total": total,
            "filters": {
                "start_time": effective_start_time.isoformat() if effective_start_time else None,
                "end_time": end_time.isoformat() if end_time else None,
                "event_type": event_type,
                "status": status,
                "user_id": user_id,
                "tool_name": tool_name,
                "keyword": keyword,
            },
        },
    )

    return AuditLogListResponse(
        items=[_to_response(item, include_details=include_details) for item in logs],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{log_id}", response_model=AuditLogResponse)
async def get_audit_log_detail(
    log_id: int,
    request: Request,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    """审计日志详情（第五步：审计查询行为回写）"""
    query = _apply_role_scope(select(AuditLog).where(AuditLog.id == log_id), current_user)
    result = await db.execute(query)
    log_item = result.scalars().first()
    if not log_item:
        raise HTTPException(status_code=404, detail="Audit log not found")

    await _write_audit_view(
        db=db,
        request=request,
        current_user=current_user,
        details={
            "action": "detail",
            "target_log_id": log_id,
            "target_event_type": log_item.event_type,
        },
    )

    return AuditLogResponse.model_validate(log_item)
