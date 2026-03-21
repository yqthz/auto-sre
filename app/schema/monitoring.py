"""
监控数据模型
"""
from pydantic import BaseModel
from typing import List, Optional


class ContainerMetricPoint(BaseModel):
    """容器指标数据点"""
    timestamp: str
    value: float
    container_name: str


class ContainerCurrentMetrics(BaseModel):
    """容器当前指标"""
    container_name: str
    cpu_percent: Optional[float] = None
    memory_bytes: Optional[int] = None
    memory_percent: Optional[float] = None
    network_in_bps: Optional[float] = None
    network_out_bps: Optional[float] = None


class ContainerHistoryMetrics(BaseModel):
    """容器历史指标"""
    container_name: str
    cpu_data: List[dict]  # [{"timestamp": "...", "value": 45.2}, ...]
    memory_data: List[dict]
    memory_percent_data: List[dict]
    network_in_data: List[dict]
    network_out_data: List[dict]


class ContainerMetricsResponse(BaseModel):
    """容器资源指标响应"""
    time_range: str
    step: str
    current: List[ContainerCurrentMetrics]
    history: List[ContainerHistoryMetrics]


class MonitoringOverviewResponse(BaseModel):
    """监控概览响应"""
    containers: List[ContainerCurrentMetrics]
    health_status: str  # healthy, warning, critical
    timestamp: str
