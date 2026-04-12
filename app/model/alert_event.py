from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON

from app.model.user import Base


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id = Column(Integer, primary_key=True, index=True)
    fingerprint = Column(String, unique=True, index=True, nullable=False)
    alert_name = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    instance = Column(String, nullable=True)
    labels = Column(JSON, nullable=False)
    annotations = Column(JSON, nullable=False)
    starts_at = Column(DateTime, nullable=False, index=True)
    ends_at = Column(DateTime, nullable=True)
    thread_id = Column(String, nullable=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True)
    analysis_status = Column(String, default="pending", nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    metrics_snapshot = Column(JSON, nullable=True)
    log_summary = Column(JSON, nullable=True)
    analysis_report = Column(JSON, nullable=True)
