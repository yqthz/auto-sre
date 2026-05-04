from datetime import datetime, timedelta
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


def _resolve_stats_window(start_time: Optional[datetime], end_time: Optional[datetime]) -> tuple[datetime, datetime]:
    end = end_time or datetime.utcnow()
    start = start_time or (end - timedelta(hours=24))
    if start > end:
        raise HTTPException(status_code=400, detail="start_time must be <= end_time")
    return start, end


def _to_utc_text(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


def _calc_p95(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(len(ordered) * 0.95) - 1
    if index < 0:
        index = 0
    if index >= len(ordered):
        index = len(ordered) - 1
    return float(ordered[index])


@router.get("/alerts/stats")
async def get_alert_stats(
    start_time: Optional[datetime] = Query(None, description="统计开始时间，ISO8601，默认最近24小时"),
    end_time: Optional[datetime] = Query(None, description="统计结束时间，ISO8601，默认当前时间"),
    top_n: int = Query(5, ge=1, le=20, description="Top 告警类型数量，默认5，最大20"),
    _current_user=Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    start, end = _resolve_stats_window(start_time, end_time)

    base_filters = [AlertEvent.created_at >= start, AlertEvent.created_at <= end]

    total_result = await db.execute(select(func.count(AlertEvent.id)).where(*base_filters))
    firing_result = await db.execute(
        select(func.count(AlertEvent.id)).where(*base_filters, AlertEvent.status == "firing")
    )
    resolved_result = await db.execute(
        select(func.count(AlertEvent.id)).where(*base_filters, AlertEvent.status == "resolved")
    )

    done_result = await db.execute(
        select(func.count(AlertEvent.id)).where(*base_filters, AlertEvent.analysis_status == "done")
    )
    failed_result = await db.execute(
        select(func.count(AlertEvent.id)).where(*base_filters, AlertEvent.analysis_status == "failed")
    )

    latency_avg_result = await db.execute(
        select(func.avg(AlertEvent.analysis_duration_sec)).where(
            *base_filters,
            AlertEvent.analysis_duration_sec.is_not(None),
        )
    )
    latency_values_result = await db.execute(
        select(AlertEvent.analysis_duration_sec).where(
            *base_filters,
            AlertEvent.analysis_duration_sec.is_not(None),
        )
    )

    error_total_result = await db.execute(
        select(func.sum(AlertEvent.log_error_warn_count)).where(*base_filters)
    )

    severity_distribution_result = await db.execute(
        select(AlertEvent.severity, func.count(AlertEvent.id).label("count"))
        .where(*base_filters)
        .group_by(AlertEvent.severity)
        .order_by(desc("count"))
    )
    top_alert_names_result = await db.execute(
        select(AlertEvent.alert_name, func.count(AlertEvent.id).label("count"))
        .where(*base_filters)
        .group_by(AlertEvent.alert_name)
        .order_by(desc("count"))
        .limit(top_n)
    )

    total_alerts = int(total_result.scalar() or 0)
    firing_alerts = int(firing_result.scalar() or 0)
    resolved_alerts = int(resolved_result.scalar() or 0)

    done = int(done_result.scalar() or 0)
    failed = int(failed_result.scalar() or 0)
    denominator = done + failed
    success_rate = round(done / denominator, 4) if denominator > 0 else 0.0

    avg_latency_raw = latency_avg_result.scalar()
    avg_latency_sec = round(float(avg_latency_raw), 2) if avg_latency_raw is not None else 0.0
    latency_values = [int(row[0]) for row in latency_values_result.all() if row and row[0] is not None]
    p95_latency_sec = _calc_p95(latency_values)

    log_error_warn_total = int(error_total_result.scalar() or 0)
    log_error_warn_avg_per_alert = round(log_error_warn_total / total_alerts, 2) if total_alerts > 0 else 0.0

    severity_distribution = [
        {"name": str(row[0] or "unknown"), "count": int(row[1] or 0)}
        for row in severity_distribution_result.all()
    ]
    top_alert_names = [
        {"name": str(row[0] or "unknown"), "count": int(row[1] or 0)}
        for row in top_alert_names_result.all()
    ]

    return {
        "window": {
            "start_time": _to_utc_text(start),
            "end_time": _to_utc_text(end),
        },
        "volume": {
            "total_alerts": total_alerts,
            "firing_alerts": firing_alerts,
            "resolved_alerts": resolved_alerts,
        },
        "analysis": {
            "done": done,
            "failed": failed,
            "success_rate": success_rate,
        },
        "latency": {
            "avg_sec": avg_latency_sec,
            "p95_sec": p95_latency_sec,
        },
        "error_signal": {
            "total_error_warn": log_error_warn_total,
            "avg_per_alert": log_error_warn_avg_per_alert,
        },
        "distribution": {
            "severity": severity_distribution,
            "alert_name_top": top_alert_names,
        },
    }


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
                session_id=event.session_id,
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
        session_id=event.session_id,
        metrics_snapshot=event.metrics_snapshot,
        log_summary=event.log_summary,
        analysis_report=event.analysis_report,
    )
