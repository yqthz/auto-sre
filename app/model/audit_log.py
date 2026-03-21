from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, JSON
from app.model.user import Base

class AuditLog(Base):
    """审计日志表 - 记录所有用户操作"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)

    # 时间戳
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # 用户信息
    user_id = Column(String, nullable=False, index=True)
    user_role = Column(String, nullable=False)

    # 事件类型：login, logout, tool_call_request, tool_call_result, tool_call_denied
    event_type = Column(String, nullable=False, index=True)

    # 工具相关（如果是工具调用）
    tool_name = Column(String, nullable=True, index=True)
    tool_permission = Column(String, nullable=True)  # info, moderate, danger

    # 详细信息（JSON 格式存储参数、结果等）
    details = Column(JSON, nullable=True)

    # 执行结果：success, failed, denied
    status = Column(String, nullable=True, index=True)

    # IP 地址和 User-Agent（用于安全分析）
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    # 错误信息（如果有）
    error_message = Column(Text, nullable=True)
