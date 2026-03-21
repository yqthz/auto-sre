"""
AI Chat 消息 API
处理消息发送、获取历史消息、流式响应等
"""
import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.model.user import User
from app.model.chat import ChatSession, ChatMessage
from app.schema.chat import (
    ChatMessageCreate,
    ChatMessageResponse,
    ChatMessageListResponse,
    ToolApprovalRequest
)
from app.service.chat_service import chat_service
from app.core.logger import logger

router = APIRouter()


@router.get("/sessions/{session_id}/messages", response_model=ChatMessageListResponse)
async def get_messages(
    session_id: int,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """获取会话的历史消息"""
    # 验证会话所有权
    session_query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id
    )
    session_result = await db.execute(session_query)
    session = session_result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 获取消息
    query = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    messages = result.scalars().all()

    # 统计总数
    from sqlalchemy import func
    count_query = select(func.count(ChatMessage.id)).where(
        ChatMessage.session_id == session_id
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    return ChatMessageListResponse(
        messages=[ChatMessageResponse.model_validate(msg) for msg in messages],
        total=total
    )


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(
    session_id: int,
    message_in: ChatMessageCreate,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    发送消息并流式返回 Agent 响应（SSE 格式）

    返回 Server-Sent Events (SSE) 流，事件格式：
    event: <event_type>
    data: <json_data>

    事件类型：
    - user_message: 用户消息已保存
    - agent_thinking: Agent 正在思考
    - agent_message_delta: Agent 消息增量
    - tool_call_start: 工具调用开始
    - tool_call_result: 工具调用结果
    - tool_approval_required: 需要用户授权
    - agent_message_complete: Agent 消息完成
    - error: 错误
    """
    # 验证会话所有权
    session_query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id
    )
    session_result = await db.execute(session_query)
    session = session_result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.status == "waiting":
        raise HTTPException(
            status_code=400,
            detail="Session is waiting for tool approval. Please approve or reject first."
        )

    async def event_generator():
        """SSE 事件生成器"""
        try:
            async for event in chat_service.stream_agent_response(
                db=db,
                session=session,
                user_message=message_in.content,
                user_id=current_user.id,
                user_role=current_user.role
            ):
                event_type = event["event"]
                event_data = event["data"]

                # 格式化为 SSE
                yield f"event: {event_type}\n"
                yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"

            # 发送结束标记
            yield "event: done\n"
            yield "data: {}\n\n"

        except Exception as e:
            logger.error(f"Error in event_generator: {e}", exc_info=True)
            yield "event: error\n"
            yield f"data: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"  # 禁用 Nginx 缓冲
        }
    )


@router.post("/sessions/{session_id}/messages/{message_id}/approve")
async def approve_tool_call(
    session_id: int,
    message_id: int,
    approval: ToolApprovalRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """
    授权或拒绝工具调用（流式返回继续执行的结果）

    用于敏感工具（如 restart_server）的人工审批
    返回 SSE 流，继续执行 Agent
    """
    # 验证会话所有权
    session_query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id
    )
    session_result = await db.execute(session_query)
    session = session_result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 获取消息
    message_query = select(ChatMessage).where(
        ChatMessage.id == message_id,
        ChatMessage.session_id == session_id
    )
    message_result = await db.execute(message_query)
    message = message_result.scalars().first()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    if not message.requires_approval:
        raise HTTPException(status_code=400, detail="This message does not require approval")

    if message.approval_status != "pending":
        raise HTTPException(status_code=400, detail="This message has already been processed")

    # 更新授权状态
    message.approval_status = "approved" if approval.approved else "rejected"
    session.status = "active"
    await db.commit()

    # 记录审计日志
    from app.model.audit_log import AuditLog
    audit = AuditLog(
        user_id=str(current_user.id),
        user_role=current_user.role,
        event_type="tool_approval",
        tool_name=message.tool_name,
        status="approved" if approval.approved else "rejected",
        details={
            "message_id": message_id,
            "tool_calls": message.tool_calls,
            "reason": approval.reason
        }
    )
    db.add(audit)
    await db.commit()

    # 流式返回继续执行的结果
    async def event_generator():
        try:
            async for event in chat_service.continue_agent_execution(
                db=db,
                session=session,
                user_id=current_user.id,
                user_role=current_user.role,
                approved=approval.approved,
                rejection_reason=approval.reason
            ):
                event_type = event["event"]
                event_data = event["data"]

                yield f"event: {event_type}\n"
                yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"

            yield "event: done\n"
            yield "data: {}\n\n"

        except Exception as e:
            logger.error(f"Error in approval event_generator: {e}", exc_info=True)
            yield "event: error\n"
            yield f"data: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.delete("/sessions/{session_id}/messages/{message_id}")
async def delete_message(
    session_id: int,
    message_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """删除单条消息（仅限用户自己的消息）"""
    # 验证会话所有权
    session_query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id
    )
    session_result = await db.execute(session_query)
    session = session_result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 获取消息
    message_query = select(ChatMessage).where(
        ChatMessage.id == message_id,
        ChatMessage.session_id == session_id
    )
    message_result = await db.execute(message_query)
    message = message_result.scalars().first()

    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    await db.delete(message)
    await db.commit()

    return {"message": "Message deleted successfully"}
