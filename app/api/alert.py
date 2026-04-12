from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.model.alert_event import AlertEvent
from app.schema.alert_event import (
    AlertEventDetailResponse,
    AlertEventListItem,
    AlertEventListResponse,
)

router = APIRouter()


@router.get("/alerts", response_model=AlertEventListResponse)
async def list_alerts(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    _current_user=Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    query = select(AlertEvent)
    count_query = select(func.count(AlertEvent.id))

    if status:
        query = query.where(AlertEvent.status == status)
        count_query = count_query.where(AlertEvent.status == status)
    if severity:
        query = query.where(AlertEvent.severity == severity)
        count_query = count_query.where(AlertEvent.severity == severity)

    offset = (page - 1) * limit
    query = query.order_by(desc(AlertEvent.starts_at)).offset(offset).limit(limit)

    result = await db.execute(query)
    events = result.scalars().all()

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    now = datetime.utcnow()
    items = []
    for event in events:
        end_time = event.ends_at or now
        duration_seconds = int((end_time - event.starts_at).total_seconds())
        if duration_seconds < 0:
            duration_seconds = 0

        items.append(
            AlertEventListItem(
                id=event.id,
                alert_name=event.alert_name,
                severity=event.severity,
                status=event.status,
                instance=event.instance,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                duration_seconds=duration_seconds,
                analysis_status=event.analysis_status,
            )
        )

    return AlertEventListResponse(items=items, total=total, page=page, limit=limit)


@router.get("/alerts/{alert_id}", response_model=AlertEventDetailResponse)
async def get_alert_detail(
    alert_id: int,
    _current_user=Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    result = await db.execute(select(AlertEvent).where(AlertEvent.id == alert_id))
    event = result.scalars().first()
    if not event:
        raise HTTPException(status_code=404, detail="Alert event not found")

    return AlertEventDetailResponse(
        id=event.id,
        alert_name=event.alert_name,
        severity=event.severity,
        status=event.status,
        instance=event.instance,
        labels=event.labels or {},
        annotations=event.annotations or {},
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        analysis_status=event.analysis_status,
        metrics_snapshot=event.metrics_snapshot,
        log_summary=event.log_summary,
        analysis_report=event.analysis_report,
    )
