from typing import Dict, List
from typing import Optional

from pydantic import BaseModel


class AlertInfo(BaseModel):
    status: str
    labels: Dict[str, str]
    annotations: Dict[str, str]
    startsAt: str
    endsAt: Optional[str] = None
    fingerprint: Optional[str] = None

class WebhookPayload(BaseModel):
    version: str
    status: str
    alerts: List[AlertInfo]
