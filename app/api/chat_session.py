from datetime import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, desc

from app.api import deps
from app.model.user import User
from app.model.chat import ChatSession, ChatMessage
from app.schema.chat import (
    ChatSessionCreate,
    ChatSessionUpdate,
    ChatSessionResponse,
    ChatSessionListResponse
)
from app.utils.format_utils import gen_id

router = APIRouter()


@router.post("/sessions", response_model=ChatSessionResponse)
async def create_session(
    session_in: ChatSessionCreate,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """创建新的聊天会话"""
    thread_id = gen_id("chat")

    session = ChatSession(
        thread_id=thread_id,
        user_id=current_user.id,
        title=session_in.title,
        mode=session_in.mode,
        status="active"
    )

    db.add(session)
    await db.commit()
    await db.refresh(session)

    # 添加消息计数
    response = ChatSessionResponse.model_validate(session)
    response.message_count = 0

    return response


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_sessions(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """获取用户的会话列表（按最后消息时间倒序）"""
    # 查询会话
    query = (
        select(ChatSession)
        .where(ChatSession.user_id == current_user.id)
        .order_by(desc(ChatSession.last_message_at))
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(query)
    sessions = result.scalars().all()

    # 统计总数
    count_query = select(func.count(ChatSession.id)).where(
        ChatSession.user_id == current_user.id
    )
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # 为每个会话添加消息计数
    session_responses = []
    for session in sessions:
        msg_count_query = select(func.count(ChatMessage.id)).where(
            ChatMessage.session_id == session.id
        )
        msg_count_result = await db.execute(msg_count_query)
        msg_count = msg_count_result.scalar()

        response = ChatSessionResponse.model_validate(session)
        response.message_count = msg_count
        session_responses.append(response)

    return ChatSessionListResponse(sessions=session_responses, total=total)


@router.get("/sessions/{session_id}", response_model=ChatSessionResponse)
async def get_session(
    session_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """获取单个会话详情"""
    query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id
    )
    result = await db.execute(query)
    session = result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 添加消息计数
    msg_count_query = select(func.count(ChatMessage.id)).where(
        ChatMessage.session_id == session.id
    )
    msg_count_result = await db.execute(msg_count_query)
    msg_count = msg_count_result.scalar()

    response = ChatSessionResponse.model_validate(session)
    response.message_count = msg_count

    return response


@router.patch("/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(
    session_id: int,
    session_update: ChatSessionUpdate,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """更新会话（标题、状态等）"""
    query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id
    )
    result = await db.execute(query)
    session = result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    # 更新字段
    if session_update.title is not None:
        session.title = session_update.title
    if session_update.status is not None:
        session.status = session_update.status

    session.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(session)

    # 添加消息计数
    msg_count_query = select(func.count(ChatMessage.id)).where(
        ChatMessage.session_id == session.id
    )
    msg_count_result = await db.execute(msg_count_query)
    msg_count = msg_count_result.scalar()

    response = ChatSessionResponse.model_validate(session)
    response.message_count = msg_count

    return response


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db)
):
    """删除会话（级联删除所有消息）"""
    query = select(ChatSession).where(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id
    )
    result = await db.execute(query)
    session = result.scalars().first()

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.delete(session)
    await db.commit()

    return {"message": "Session deleted successfully"}
