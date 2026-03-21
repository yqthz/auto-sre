"""
监控 API 路由
"""
from fastapi import APIRouter, Depends, Query
from typing import Optional

from app.api.deps import get_current_user
from app.schema.monitoring import ContainerMetricsResponse, MonitoringOverviewResponse
from app.service.monitoring_service import monitoring_service
# from app.core.security import get_current_user

router = APIRouter(prefix="/api/v1/monitoring", tags=["monitoring"])


@router.get("/containers/metrics", response_model=ContainerMetricsResponse)
async def get_container_metrics(
    time_range: str = Query("1h", description="时间范围: 1h, 6h, 24h, 7d"),
    step: str = Query("1m", description="数据点间隔: 15s, 1m, 5m, 1h"),
    container: Optional[str] = Query(None, description="容器名称过滤"),
    current_user: dict = Depends(get_current_user)
):
    """
    获取容器资源指标

    - **time_range**: 时间范围（1h, 6h, 24h, 7d）
    - **step**: 数据点间隔（15s, 1m, 5m, 1h）
    - **container**: 容器名称过滤（可选）

    返回容器的 CPU、内存、网络使用情况，包括当前值和历史趋势
    """
    return await monitoring_service.get_container_metrics(time_range, step, container)


@router.get("/overview", response_model=MonitoringOverviewResponse)
async def get_monitoring_overview(
    current_user: dict = Depends(get_current_user)
):
    """
    获取监控概览

    返回所有容器的当前资源使用情况和整体健康状态
    """
    return await monitoring_service.get_monitoring_overview()
