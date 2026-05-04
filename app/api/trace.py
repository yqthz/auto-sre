"""
Trace APIs for the standalone trace console.
"""
import asyncio
import json
import time
from datetime import timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.agent.trace_runtime import trace_runtime
from app.api import deps
from app.model.chat import ChatSession
from app.model.user import User

router = APIRouter()
AUTO_BOT_EMAIL = "system-autobot@auto-sre.local"


def _datetime_to_iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


async def _get_owned_session(
    db: AsyncSession,
    *,
    session_id: int,
    user_id: int,
    visible_user_ids: Optional[list[int]] = None,
) -> ChatSession:
    allowed_user_ids = [user_id]
    if visible_user_ids:
        allowed_user_ids.extend(visible_user_ids)

    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id.in_(allowed_user_ids),
        )
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


async def _get_auto_bot_user_id(db: AsyncSession) -> Optional[int]:
    result = await db.execute(select(User.id).where(User.email == AUTO_BOT_EMAIL))
    bot_user_id = result.scalar_one_or_none()
    return int(bot_user_id) if bot_user_id is not None else None


async def _visible_trace_user_ids(db: AsyncSession) -> list[int]:
    bot_user_id = await _get_auto_bot_user_id(db)
    return [bot_user_id] if bot_user_id is not None else []


def _session_payload(session: ChatSession) -> dict:
    return {
        "id": session.id,
        "thread_id": session.thread_id,
        "user_id": session.user_id,
        "title": session.title,
        "mode": session.mode,
        "status": session.status,
        "created_at": _datetime_to_iso(session.created_at),
        "updated_at": _datetime_to_iso(session.updated_at),
        "last_message_at": _datetime_to_iso(session.last_message_at),
    }


@router.get("/runs")
async def list_trace_runs(
    session_id: Optional[int] = None,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    visible_user_ids = await _visible_trace_user_ids(db)
    return trace_runtime.list_runs(
        user_id=current_user.id,
        visible_user_ids=visible_user_ids,
        session_id=session_id,
        status=status,
        mode=mode,
        skip=skip,
        limit=min(max(limit, 0), 200),
    )


@router.get("/runs/{run_id}")
async def get_trace_run(
    run_id: str,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    summary = trace_runtime.get_run(run_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Trace run not found")
    if not trace_runtime.check_owner(
        run_id,
        current_user.id,
        visible_user_ids=await _visible_trace_user_ids(db),
    ):
        raise HTTPException(status_code=403, detail="Forbidden")
    return summary


@router.get("/runs/{run_id}/events")
async def get_trace_events(
    run_id: str,
    since_seq: int = 0,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    if not trace_runtime.check_owner(
        run_id,
        current_user.id,
        visible_user_ids=await _visible_trace_user_ids(db),
    ):
        raise HTTPException(status_code=403, detail="Forbidden")

    payload = trace_runtime.get_events(run_id, since_seq=since_seq)
    if not payload.get("exists"):
        raise HTTPException(status_code=404, detail="Trace run not found")
    return payload


@router.get("/runs/{run_id}/stream")
async def stream_trace_events(
    run_id: str,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    if not trace_runtime.check_owner(
        run_id,
        current_user.id,
        visible_user_ids=await _visible_trace_user_ids(db),
    ):
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


@router.get("/sessions")
async def list_trace_sessions(
    status: Optional[str] = None,
    mode: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    visible_user_ids = await _visible_trace_user_ids(db)
    trace_summaries = trace_runtime.list_session_summaries(
        user_id=current_user.id,
        visible_user_ids=visible_user_ids,
        status=status,
        mode=mode,
    )
    if not trace_summaries:
        return {"sessions": [], "total": 0}

    session_ids = [int(summary["session_id"]) for summary in trace_summaries]
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.id.in_(session_ids),
            ChatSession.user_id.in_([current_user.id, *visible_user_ids]),
        )
    )
    sessions_by_id = {session.id: session for session in result.scalars().all()}

    rows = []
    for trace_summary in trace_summaries:
        session = sessions_by_id.get(int(trace_summary["session_id"]))
        if session is None:
            continue
        rows.append({
            "session": _session_payload(session),
            "trace": trace_summary,
        })

    total = len(rows)
    start = max(skip, 0)
    end = start + min(max(limit, 0), 200)
    return {
        "sessions": rows[start:end],
        "total": total,
    }


@router.get("/sessions/{session_id}")
async def get_trace_session(
    session_id: int,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    session = await _get_owned_session(
        db,
        session_id=session_id,
        user_id=current_user.id,
        visible_user_ids=await _visible_trace_user_ids(db),
    )
    return {
        "session": _session_payload(session),
        "trace": trace_runtime.get_session_summary(
            session_id=session_id,
            user_id=session.user_id,
        ),
    }


@router.get("/sessions/{session_id}/runs")
async def list_session_trace_runs(
    session_id: int,
    status: Optional[str] = None,
    mode: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    session = await _get_owned_session(
        db,
        session_id=session_id,
        user_id=current_user.id,
        visible_user_ids=await _visible_trace_user_ids(db),
    )
    return trace_runtime.list_runs(
        user_id=session.user_id,
        session_id=session_id,
        status=status,
        mode=mode,
        skip=skip,
        limit=min(max(limit, 0), 200),
    )


@router.get("/sessions/{session_id}/events")
async def get_session_trace_events(
    session_id: int,
    since_ts: float = 0,
    limit: int = 500,
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db),
):
    session = await _get_owned_session(
        db,
        session_id=session_id,
        user_id=current_user.id,
        visible_user_ids=await _visible_trace_user_ids(db),
    )
    return trace_runtime.get_session_events(
        session_id=session_id,
        user_id=session.user_id,
        since_ts=since_ts,
        limit=min(max(limit, 0), 2000),
    )
