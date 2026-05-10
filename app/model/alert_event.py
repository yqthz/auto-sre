from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON

from app.model.user import Base


class AlertEvent(Base):
    """告警事件模型

    记录单条告警的基础信息、生命周期时间点，以及自动分析过程的状态与结果
    """

    __tablename__ = "alert_events"

    # 唯一标识同一条告警事件（通常由上游告警系统生成）
    id = Column(Integer, primary_key=True, index=True)
    fingerprint = Column(String, unique=True, index=True, nullable=False)
    alert_name = Column(String, nullable=False, index=True)
    severity = Column(String, nullable=False)

    # 告警当前状态，例如 firing / resolved
    status = Column(String, nullable=False, index=True)
    instance = Column(String, nullable=True)

    # 原始告警上下文：labels 用于检索，annotations 用于展示说明
    labels = Column(JSON, nullable=False)
    annotations = Column(JSON, nullable=False)

    # 告警起止时间
    starts_at = Column(DateTime, nullable=False, index=True)
    ends_at = Column(DateTime, nullable=True)

    # 关联的会话上下文，用于追踪告警分析过程
    thread_id = Column(String, nullable=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True)

    # 自动分析任务状态与耗时统计
    analysis_status = Column(String, default="pending", nullable=False, index=True)
    analysis_started_at = Column(DateTime, nullable=True)
    analysis_completed_at = Column(DateTime, nullable=True)
    analysis_duration_sec = Column(Integer, nullable=True)

    # 分析阶段日志中的 error/warn 聚合计数

    # 记录创建和更新时刻
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # 分析产物：指标快照、日志摘要、结构化分析报告
    metrics_snapshot = Column(JSON, nullable=True)
    log_summary = Column(JSON, nullable=True)
    analysis_report = Column(JSON, nullable=True)
