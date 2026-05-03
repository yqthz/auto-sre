from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditLogResponse(BaseModel):
    id: int
    timestamp: datetime
    event_type: str
    status: str | None = None
    user_id: str
    user_role: str
    tool_name: str | None = None
    tool_permission: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    error_message: str | None = None
    details: dict[str, Any] | None = None

    class Config:
        from_attributes = True


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
    page: int
    page_size: int
