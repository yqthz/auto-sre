"""
AI Chat message APIs.
"""
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.agent.approval_policy import check_approval_permission, tool_approval_profile
from app.api import deps
from app.core.logger import logger
from app.agent.trace_runtime import trace_runtime
from app.model.audit_log import AuditLog
from app.model.chat import ChatMessage, ChatSession
from app.model.user import User
from app.schema.chat import (
    ChatMessageCreate,
    ChatMessageListResponse,
    ChatMessageResponse,
    ToolApprovalRequest,
)
from app.service.chat_service import chat_service

router = APIRouter()


def _extract_approval_request(message: ChatMessage) -> dict:
    """解析审批上下文"""
    tool_calls = message.tool_calls or []
    if isinstance(tool_calls, list) and tool_calls:
        first_call = tool_calls[0]
        if isinstance(first_call, dict):
            request = first_call.get("approval_request")
            if isinstance(request, dict):
                return request

            tool_name = first_call.get("name") or message.tool_name or "unknown"
            tool_args = first_call.get("args", {})
            profile = tool_approval_profile(tool_name, tool_args)
            return {
                "tool_call_id": first_call.get("id"),
                "tool_name": tool_name,
                "permission": str(profile.get("permission") or "unknown"),
                "risk_level": str(profile.get("risk_level") or "low"),
                "args": tool_args,
            }
    return {}


async def _write_approval_audit(
    db: AsyncSession,
    *,
    current_user: User,
    message: ChatMessage,
    decision_status: str,
    detail_extra: dict,
):
    """写入审计日志"""
    approval_request = _extract_approval_request(message)
    audit = AuditLog(
        user_id=str(current_user.id),
        user_role=current_user.role,
        event_type="approval_decision",
        tool_name=message.tool_name or approval_request.get("tool_name"),
        tool_permission=approval_request.get("permission"),
        status=decision_status,
        details={
            "message_id": message.id,
            "session_id": message.session_id,
            "approval_request": approval_request,
            "decided_at": datetime.now(timezone.utc).isoformat(),
            **detail_extra,
        },
    )
    db.add(audit)
    await db.commit()


@router.get("/sessions/{session_id}/messages", response_model=ChatMessageListResponse)
async def get_messages(
    session_id: int,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    """获取聊天信息"""

    # 校验会话
    session_query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id,
    )
    session_result = await db.execute(session_query)
    session = session_result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 获取消息列表
    query = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    messages = result.scalars().all()

    from sqlalchemy import func

    # 统计该会话消息的总条数
    count_query = select(func.count(ChatMessage.id)).where(
        ChatMessage.session_id == session_id,
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    return ChatMessageListResponse(
        messages=[ChatMessageResponse.model_validate(msg) for msg in messages],
        total=total,
    )


@router.post("/sessions/{session_id}/messages/stream")
async def send_message_stream(
    session_id: int,
    message_in: ChatMessageCreate,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    """流式返回 AI 消息"""

    # 校验会话
    session_query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id,
    )
    session_result = await db.execute(session_query)
    session = session_result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 会话状态检查
    if session.status == "waiting":
        raise HTTPException(
            status_code=400,
            detail="Session is waiting for tool approval. Please approve or reject first.",
        )

    async def event_generator():
        try:
            async for event in chat_service.stream_agent_response(
                db=db,
                session=session,
                user_message=message_in.content,
                user_id=current_user.id,
                user_role=current_user.role,
            ):
                event_type = event["event"]
                event_data = event["data"]
                yield f"event: {event_type}\n"
                yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"

            yield "event: done\n"
            yield "data: {}\n\n"
        except Exception as e:
            logger.error(f"Error in event_generator: {e}", exc_info=True)
            yield "event: error\n"
            yield f"data: {json.dumps({'message': str(e)}, ensure_ascii=False)}\n\n"

    # 流式返回
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/messages/{message_id}/approve")
async def approve_tool_call(
    session_id: int,
    message_id: int,
    approval: ToolApprovalRequest,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    """审批工具调用"""

    # 校验会话
    session_query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id,
    )
    session_result = await db.execute(session_query)
    session = session_result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 获取消息
    message_query = select(ChatMessage).where(
        ChatMessage.id == message_id,
        ChatMessage.session_id == session_id,
    )
    message_result = await db.execute(message_query)
    message = message_result.scalars().first()

    # 校验消息状态是否可审批
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    if not message.requires_approval:
        raise HTTPException(status_code=400, detail="This message does not require approval")

    if message.approval_status != "pending":
        raise HTTPException(status_code=400, detail="This message has already been processed")

    # 解析审批上下文
    approval_request = _extract_approval_request(message)
    risk_level = approval_request.get("risk_level", "high")
    tool_name = approval_request.get("tool_name") or message.tool_name

    # 权限策略检查
    allowed, deny_reason = check_approval_permission(
        risk_level,
        current_user.role,
        tool_name=tool_name,
    )
    if not allowed:
        # 更新 agent 运行状态
        chat_service.record_approval_outcome(
            session=session,
            status="policy_denied",
            approved=False,
            actor_id=current_user.id,
            actor_role=current_user.role,
            reason=deny_reason,
        )
        # 写入审计日志
        await _write_approval_audit(
            db,
            current_user=current_user,
            message=message,
            decision_status="policy_denied",
            detail_extra={
                "approved": False,
                "reason": deny_reason,
                "risk_level": risk_level,
            },
        )
        raise HTTPException(status_code=403, detail=deny_reason)

    # 写入审批决定
    message.approval_status = "approved" if approval.approved else "rejected"
    session.status = "active"
    await db.commit()

    # 写入审计日志
    await _write_approval_audit(
        db,
        current_user=current_user,
        message=message,
        decision_status="approved" if approval.approved else "rejected",
        detail_extra={
            "approved": approval.approved,
            "reason": approval.reason,
            "risk_level": risk_level,
        },
    )

    # 继续 agent 执行
    async def event_generator():
        try:
            async for event in chat_service.continue_agent_execution(
                db=db,
                session=session,
                user_id=current_user.id,
                user_role=current_user.role,
                approved=approval.approved,
                rejection_reason=approval.reason,
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

    # 流式返回
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# TODO: 
# @router.delete("/sessions/{session_id}/messages/{message_id}")
# async def delete_message(
#     session_id: int,
#     message_id: int,
#     current_user: User = Depends(deps.get_current_active_user),
#     db: AsyncSession = Depends(deps.get_db),
# ):
#     session_query = select(ChatSession).where(
#         ChatSession.id == session_id,
#         ChatSession.user_id == current_user.id,
#     )
#     session_result = await db.execute(session_query)
#     session = session_result.scalars().first()

#     if not session:
#         raise HTTPException(status_code=404, detail="Session not found")

#     message_query = select(ChatMessage).where(
#         ChatMessage.id == message_id,
#         ChatMessage.session_id == session_id,
#     )
#     message_result = await db.execute(message_query)
#     message = message_result.scalars().first()

#     if not message:
#         raise HTTPException(status_code=404, detail="Message not found")

#     await db.delete(message)
#     await db.commit()

#     return {"message": "Message deleted successfully"}

@router.get("/trace/runs/{run_id}")
async def get_trace_run(
    run_id: str,
    current_user: User = Depends(deps.get_current_active_user),
):
    summary = trace_runtime.get_run(run_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Trace run not found")
    if not trace_runtime.check_owner(run_id, current_user.id):
        raise HTTPException(status_code=403, detail="Forbidden")
    return summary


@router.get("/trace/runs/{run_id}/events")
async def get_trace_events(
    run_id: str,
    since_seq: int = 0,
    current_user: User = Depends(deps.get_current_active_user),
):
    if not trace_runtime.check_owner(run_id, current_user.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    payload = trace_runtime.get_events(run_id, since_seq=since_seq)
    if not payload.get("exists"):
        raise HTTPException(status_code=404, detail="Trace run not found")
    return payload


@router.get("/trace/runs/{run_id}/stream")
async def stream_trace_events(
    run_id: str,
    current_user: User = Depends(deps.get_current_active_user),
):
    if not trace_runtime.check_owner(run_id, current_user.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    queue = trace_runtime.subscribe(run_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="Trace run not found")

    async def event_generator():
        heartbeat_seconds = 15.0
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield "event: heartbeat\n"
                    yield f"data: {json.dumps({'ts': time.time()}, ensure_ascii=False)}\n\n"
                    continue

                if item is None:
                    yield "event: done\n"
                    yield "data: {}\n\n"
                    return

                event_type = str(item.get("type") or "event")
                yield f"event: {event_type}\n"
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"

                if event_type == "run_end":
                    return
        finally:
            trace_runtime.unsubscribe(run_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
