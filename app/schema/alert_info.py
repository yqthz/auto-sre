from typing import Dict, List

from pydantic import BaseModel


class AlertInfo(BaseModel):
    status: str
    labels: Dict[str, str]
    annotations: Dict[str, str]
    startsAt: str
    endsAt: str

class WebhookPayload(BaseModel):
    version: str
    status: str
    alerts: List[AlertInfo]
