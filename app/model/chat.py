from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, JSON
from sqlalchemy.orm import relationship
from app.model.user import Base


class ChatSession(Base):
    """AI Chat 会话表"""
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)

    # 会话标识（用于 LangGraph thread_id）
    thread_id = Column(String, unique=True, nullable=False, index=True)

    # 用户信息
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # 会话标题（自动生成或用户修改）
    title = Column(String, default="新对话", nullable=False)

    # 会话模式：manual（人工交互）或 auto（自动运行）
    mode = Column(String, default="manual", nullable=False)

    # 会话状态：active（进行中）、waiting（等待授权）、completed（已完成）
    status = Column(String, default="active", nullable=False, index=True)

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # 最后一条消息时间（用于排序）
    last_message_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # 关联的告警信息（如果是从告警触发的）
    alert_context = Column(JSON, nullable=True)

    # 关联消息
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    """AI Chat 消息表"""
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)

    # 所属会话
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)

    # 消息角色：user（用户）、assistant（AI）、tool（工具调用）、system（系统）
    role = Column(String, nullable=False)

    # 消息内容
    content = Column(Text, nullable=True)

    # 工具调用信息（JSON 格式）
    tool_calls = Column(JSON, nullable=True)

    # 工具调用 ID（用于关联工具结果）
    tool_call_id = Column(String, nullable=True)

    # 工具名称
    tool_name = Column(String, nullable=True)

    # 消息状态：pending（等待中）、streaming（流式输出中）、completed（完成）、error（错误）
    status = Column(String, default="completed", nullable=False)

    # 是否需要用户授权（针对敏感工具）
    requires_approval = Column(Boolean, default=False, nullable=False)

    # 授权状态：null（不需要）、pending（等待）、approved（已批准）、rejected（已拒绝）
    approval_status = Column(String, nullable=True)

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # 关联会话
    session = relationship("ChatSession", back_populates="messages")
