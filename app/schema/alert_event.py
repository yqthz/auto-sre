from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class AlertEventListItem(BaseModel):
    id: int
    alert_name: str
    severity: str
    status: str
    instance: Optional[str]
    starts_at: datetime
    ends_at: Optional[datetime]
    duration_seconds: int
    analysis_status: str
    session_id: Optional[int]

    class Config:
        from_attributes = True


class AlertEventListResponse(BaseModel):
    items: List[AlertEventListItem]
    total: int
    page: int
    limit: int


class AlertEventDetailResponse(BaseModel):
    id: int
    alert_name: str
    severity: str
    status: str
    instance: Optional[str]
    labels: Dict[str, str]
    annotations: Dict[str, str]
    starts_at: datetime
    ends_at: Optional[datetime]
    analysis_status: str
    session_id: Optional[int]
    metrics_snapshot: Optional[Dict[str, Any]]
    log_summary: Optional[Dict[str, Any]]
    report_markdown: Optional[str]

    class Config:
        from_attributes = True
