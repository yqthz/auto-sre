from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel


# ============ 会话相关 Schema ============

class ChatSessionCreate(BaseModel):
    """创建会话请求"""
    title: Optional[str] = "新对话"
    mode: str = "manual"  # manual 或 auto


class ChatSessionUpdate(BaseModel):
    """更新会话请求"""
    title: Optional[str] = None
    status: Optional[str] = None


class ChatSessionResponse(BaseModel):
    """会话响应"""
    id: int
    thread_id: str
    user_id: int
    title: str
    mode: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime
    message_count: Optional[int] = 0  # 消息数量

    class Config:
        from_attributes = True


class ChatSessionListResponse(BaseModel):
    """会话列表响应"""
    sessions: List[ChatSessionResponse]
    total: int


# ============ 消息相关 Schema ============

class ToolCallInfo(BaseModel):
    """工具调用信息"""
    id: str
    name: str
    args: dict


class ChatMessageCreate(BaseModel):
    """创建消息请求"""
    content: str


class ChatMessageResponse(BaseModel):
    """消息响应"""
    id: int
    session_id: int
    role: str
    content: Optional[str]
    tool_calls: Optional[List[dict]] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    status: str
    requires_approval: bool
    approval_status: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ChatMessageListResponse(BaseModel):
    """消息列表响应"""
    messages: List[ChatMessageResponse]
    total: int


# ============ 流式响应 Schema ============

class StreamEvent(BaseModel):
    """流式事件"""
    event: str  # message_start, content_delta, tool_call, tool_result, message_end, error
    data: Any


class ToolApprovalRequest(BaseModel):
    """工具授权请求"""
    approved: bool  # True 批准，False 拒绝
    reason: Optional[str] = None  # 拒绝原因
