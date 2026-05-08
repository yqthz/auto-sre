import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import create_graph
from app.agent.approval_policy import tool_approval_profile
from app.agent.trace import ToolTrace
from app.agent.trace_runtime import trace_runtime
from app.agent.tools.audit import audit_tool_event
from app.agent.tools.security import before_tool_execution
from app.api import deps
from app.core.logger import logger
from app.db.session import AsyncSessionLocal
from app.model.alert_event import AlertEvent
from app.model.chat import ChatSession
from app.model.user import User
from app.schema.alert_info import WebhookPayload
from app.service.audit_service import write_system_audit_log
from app.utils.format_utils import gen_id

router = APIRouter()
graph = create_graph()

AUTO_BOT_USER_ID = "system_autobot"
AUTO_BOT_ROLE = "viewer"
AUTO_BOT_EMAIL = "system-autobot@auto-sre.local"


def _safe_json_loads(raw: Any):
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_alert_time(raw: Optional[str]) -> Optional[datetime]:
    """解析告警时间"""
    if not raw:
        return None
    value = raw.strip()
    if not value or value.startswith("0001-01-01"):
        return None

    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _build_fingerprint(alert) -> str:
    # Prefer Alertmanager native fingerprint if present.
    if getattr(alert, "fingerprint", None):
        return alert.fingerprint

    alert_name = alert.labels.get("alertname", "unknown")
    instance = alert.labels.get("instance", "unknown")
    starts_at = _parse_alert_time(alert.startsAt)
    starts_at_text = starts_at.isoformat() if starts_at else alert.startsAt
    source = f"{alert_name}|{instance}|{starts_at_text}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


async def _mark_analysis_status(alert_event_id: int, status: str):
    """更新告警事件状态"""
    values = {"analysis_status": status}
    if status == "running":
        values["analysis_started_at"] = datetime.utcnow()

    async with AsyncSessionLocal() as db:
        await db.execute(
            update(AlertEvent)
            .where(AlertEvent.id == alert_event_id)
            .values(**values)
        )
        await db.commit()


async def _ensure_auto_bot_user(db: AsyncSession) -> User:
    """确保 bot user 存在"""
    result = await db.execute(select(User).where(User.email == AUTO_BOT_EMAIL))
    user = result.scalars().first()
    if user:
        return user

    user = User(
        email=AUTO_BOT_EMAIL,
        hashed_password="auto-sre-system-user",
        role=AUTO_BOT_ROLE,
        is_active=True,
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(select(User).where(User.email == AUTO_BOT_EMAIL))
        user = result.scalars().first()
        if user:
            return user
        raise
    await db.refresh(user)
    return user


async def _create_auto_trace_session(
    db: AsyncSession,
    *,
    bot_user_id: int,
    thread_id: str,
    alert_name: str,
    alert_context: Dict[str, Any],
) -> ChatSession:
    """创建自动排查 session 记录"""
    title = f"[Auto] {alert_name}"
    session = ChatSession(
        thread_id=thread_id,
        user_id=bot_user_id,
        title=title,
        mode="auto",
        status="active",
        alert_context=alert_context,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session


def _extract_log_error_warn_count(log_summary: Any) -> int:
    if not isinstance(log_summary, dict):
        return 0

    entries = log_summary.get("entries")
    if not isinstance(entries, list):
        return 0

    total = 0
    for item in entries:
        if not isinstance(item, dict):
            continue
        count = item.get("count", 0)
        try:
            total += int(count)
        except (TypeError, ValueError):
            continue

    return max(total, 0)


async def save_analysis_results(thread_id: str, alert_event_id: int, analysis_status: str):
    """保存分析结果"""
    async with AsyncSessionLocal() as db:
        # 获取 state
        config = {"configurable": {"thread_id": thread_id, "mode": "auto", "user_role": AUTO_BOT_ROLE, "user_id": AUTO_BOT_USER_ID}}
        snapshot = graph.get_state(config)
        messages = snapshot.values.get("messages", [])
        report_text = snapshot.values.get("report", "")

        metrics_snapshot = None
        log_summary = None

        # 遍历 ToolMessage
        for msg in messages:
            if not isinstance(msg, ToolMessage):
                continue

            if msg.name == "query_prometheus_metrics":
                metrics_snapshot = _safe_json_loads(msg.content)
                continue

            if msg.name == "analyze_log_around_alert":
                log_summary = _safe_json_loads(msg.content)
                continue

            if msg.name != "dispatch_tool":
                continue

            payload = _safe_json_loads(msg.content)
            if not isinstance(payload, dict) or payload.get("status") != "executed":
                continue

            action = payload.get("action")
            result = payload.get("result")
            if action == "prometheus.query_prometheus_metrics":
                metrics_snapshot = result
            elif action == "log.analyze_log_around_alert":
                log_summary = result

        # 获取分析报告
        analysis_report = _safe_json_loads(report_text)

        # 获取完成时间，开始时间
        analysis_completed_at = datetime.utcnow()
        started_at_result = await db.execute(select(AlertEvent.analysis_started_at).where(AlertEvent.id == alert_event_id))
        analysis_started_at = started_at_result.scalar_one_or_none()

        # 计算分析时间
        analysis_duration_sec = None
        if analysis_started_at:
            analysis_duration_sec = int((analysis_completed_at - analysis_started_at).total_seconds())
            if analysis_duration_sec < 0:
                analysis_duration_sec = 0

        # 计算 log_error_warn_count
        log_error_warn_count = _extract_log_error_warn_count(log_summary)

        # 更新 AlertEvent
        await db.execute(
            update(AlertEvent)
            .where(AlertEvent.id == alert_event_id)
            .values(
                metrics_snapshot=metrics_snapshot,
                log_summary=log_summary,
                analysis_report=analysis_report,
                analysis_status=analysis_status,
                analysis_completed_at=analysis_completed_at,
                analysis_duration_sec=analysis_duration_sec,
                log_error_warn_count=log_error_warn_count,
            )
        )
        await db.commit()


def run_agent(
    thread_id: str,
    alert_event_id: int,
    session_id: int,
    trace_user_id: int,
    app_loop: asyncio.AbstractEventLoop,
    initial_input: Optional[Dict] = None,
):
    """
    后台任务：驱动 Agent 自动运行。
    """
    # 创建 trace run
    run_id = trace_runtime.start_run(
        session_id=session_id,
        user_id=trace_user_id,
        mode="auto",
        trigger_type="alert",
        alert_event_id=alert_event_id,
        thread_id=thread_id,
    )

    # 构造 config
    config = {
        "configurable": {
            "thread_id": thread_id,
            "mode": "auto",
            "user_role": AUTO_BOT_ROLE,
            "user_id": AUTO_BOT_USER_ID,
            "trace_run_id": run_id,
        }
    }
    if initial_input is not None:
        initial_input["trace_run_id"] = run_id

    current_input = initial_input
    success = True
    logger.info(f"Thread {thread_id}: Starting loop")

    # 告警分析状态改为 running
    asyncio.run_coroutine_threadsafe(
        _mark_analysis_status(alert_event_id, "running"), app_loop
    ).result()

    while True:
        try:
            events = graph.stream(current_input, config=config, stream_mode="values")
            for event in events:
                if "message" in event:
                    msg = event["message"][-1]

                    if isinstance(msg, ToolMessage):
                        ToolTrace.on_tool_end(
                            run_id=run_id,
                            call_id=msg.tool_call_id,
                            tool_name=str(msg.name or "unknown"),
                            output_preview=str(msg.content),
                            status="success",
                        )
                        # 记录工具调用执行结果
                        dispatch_extra: Dict[str, Any] = {}
                        if str(msg.name or "") == "dispatch_tool":
                            dispatch_payload = _safe_json_loads(msg.content)
                            if isinstance(dispatch_payload, dict):
                                dispatch_extra = {
                                    "action": dispatch_payload.get("action"),
                                    "dispatch_status": dispatch_payload.get("status"),
                                    "execution_backend": dispatch_payload.get("execution_backend"),
                                    "attempts": dispatch_payload.get("attempts"),
                                    "last_error_kind": dispatch_payload.get("last_error_kind"),
                                    "risk_level": dispatch_payload.get("risk_level"),
                                    "requires_approval": dispatch_payload.get("requires_approval"),
                                }
                        audit_tool_event(
                            "tool_call_result",
                            tool=str(msg.name or "unknown"),
                            user_id=AUTO_BOT_USER_ID,
                            user_role=AUTO_BOT_ROLE,
                            mode="auto",
                            thread_id=thread_id,
                            trace_run_id=run_id,
                            tool_call_id=msg.tool_call_id,
                            result=str(msg.content),
                            status="success",
                            extra={
                                "session_id": session_id,
                                "alert_event_id": alert_event_id,
                                **dispatch_extra,
                            },
                        )
                        logger.info(f"Tool {msg.name} executed. Result len: {len(msg.content)}")

            # 获取图状态
            snapshot = graph.get_state(config)
            # 如果没有下一步，直接结束
            if not snapshot.next:
                logger.info(f"Thread {thread_id}: Process finished successfully.")
                break

            # 如果下一步是工具调用
            if snapshot.next[0] == "tools":
                last_message = snapshot.values["messages"][-1]
                if isinstance(last_message, AIMessage) and last_message.tool_calls:
                    tool_calls = last_message.tool_calls

                    all_checks_passed = True
                    for tc in tool_calls:
                        # 对每个 tool call 进行安全检查
                        try:
                            before_tool_execution(
                                tool_name=tc["name"],
                                args=tc["args"],
                                user_id=AUTO_BOT_USER_ID,
                                user_role=AUTO_BOT_ROLE,
                                mode="auto",
                            )
                            ToolTrace.on_tool_start(run_id=run_id, tool_call=tc)
                            audit_tool_event(
                                "tool_call_request",
                                tool=str(tc.get("name") or "unknown"),
                                user_id=AUTO_BOT_USER_ID,
                                user_role=AUTO_BOT_ROLE,
                                mode="auto",
                                thread_id=thread_id,
                                trace_run_id=run_id,
                                tool_call_id=tc.get("id"),
                                args=tc.get("args", {}),
                                status="requested",
                                extra={
                                    "session_id": session_id,
                                    "alert_event_id": alert_event_id,
                                },
                            )
                        except PermissionError as e:
                            logger.error(f"Security check failed for {tc['name']}: {e}")
                            audit_tool_event(
                                "tool_call_denied",
                                tool=str(tc.get("name") or "unknown"),
                                user_id=AUTO_BOT_USER_ID,
                                user_role=AUTO_BOT_ROLE,
                                mode="auto",
                                thread_id=thread_id,
                                trace_run_id=run_id,
                                tool_call_id=tc.get("id"),
                                args=tc.get("args", {}),
                                error=str(e),
                                status="denied",
                                extra={
                                    "session_id": session_id,
                                    "alert_event_id": alert_event_id,
                                },
                            )

                            deny_msg = ToolMessage(
                                tool_call_id=tc["id"],
                                content=f"SecurityError: {str(e)}",
                                name=tc["name"],
                            )
                            ToolTrace.on_tool_end(
                                run_id=run_id,
                                call_id=tc.get("id"),
                                tool_name=str(tc.get("name") or "unknown"),
                                output_preview=f"SecurityError: {str(e)}",
                                status="denied",
                            )
                            graph.update_state(config, {"messages": [deny_msg]}, as_node="tools")
                            all_checks_passed = False
                            break

                    if not all_checks_passed:
                        current_input = None
                        continue

                    # 如果通过检查，检查有工具是否需要人工审批
                    tool_names = [t["name"] for t in tool_calls]
                    has_sensitive_tool = any(
                        bool(tool_approval_profile(t.get("name", ""), t.get("args", {})).get("requires_approval", False))
                        for t in tool_calls
                    )

                    # 如果有需要人工审批的工具，暂停流程
                    if has_sensitive_tool:
                        logger.warning(f"Thread {thread_id}: Paused for sensitive tools: {tool_names}")
                        success = False
                        break

                    # 没有审批的工具，自动放行
                    logger.info(f"Thread {thread_id}: Auto-approving tools: {tool_names}")
                    current_input = None
                    continue

                logger.error(f"Thread {thread_id}: Stopped at tools but no calls found.")
                success = False
                break

            break

        except Exception as e:
            logger.error(f"Thread {thread_id}: Error in execution loop - {e}", exc_info=True)
            success = False
            break

    final_status = "done" if success else "failed"
    trace_runtime.end_run(run_id, status="success" if success else "failed")
    # 保存分析结果
    asyncio.run_coroutine_threadsafe(
        save_analysis_results(thread_id, alert_event_id, final_status), app_loop
    ).result()
    # 写入审计日志
    asyncio.run_coroutine_threadsafe(
        write_system_audit_log(
            user_id=AUTO_BOT_USER_ID,
            user_role=AUTO_BOT_ROLE,
            event_type="alert_analysis",
            status="success" if success else "failed",
            details={
                "alert_event_id": alert_event_id,
                "thread_id": thread_id,
                "session_id": session_id,
                "trace_run_id": run_id,
                "analysis_status": final_status,
            },
        ),
        app_loop,
    ).result()


@router.post("/alertmanager")
async def receive_alert(
    request: WebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
):
    """
    接收 Alertmanager 告警，立即返回，后台处理。
    """
    alerts = request.alerts
    logger.info(f"Received {len(alerts)} alerts")
    app_loop = asyncio.get_running_loop()

    # 遍历每条 alert
    for alert in alerts:
        fingerprint = _build_fingerprint(alert)
        starts_at = _parse_alert_time(alert.startsAt)
        ends_at = _parse_alert_time(alert.endsAt)
        alert_name = alert.labels.get("alertname", "unknown")
        severity = alert.labels.get("severity", "unknown")
        instance = alert.labels.get("instance")

        # 告警处理
        if alert.status == "firing":
            # 确保有 bot user
            bot_user = await _ensure_auto_bot_user(db)
            # 按 fingerprint 查 AlertEvent 是否已存在
            stmt = select(AlertEvent).where(AlertEvent.fingerprint == fingerprint)
            result = await db.execute(stmt)
            existing = result.scalars().first()

            # 存在重复告警
            if existing:
                thread_id = gen_id("thread")
                alert_context = alert.model_dump() if hasattr(alert, "model_dump") else dict(alert)
                trace_session = await _create_auto_trace_session(
                    db,
                    bot_user_id=bot_user.id,
                    thread_id=thread_id,
                    alert_name=alert_name,
                    alert_context=alert_context,
                )
                initial_state = {
                "user_role": AUTO_BOT_ROLE,
                "mode": "auto",
                "alert_context": alert_context,
                "evidence": [],
                "hypotheses": [],
                "approval_requests": [],
                "actions_executed": [],
                "messages": [HumanMessage(content=f"检测到重复告警，继续执行自动排查：{alert.labels}")],
                }
                logger.info(
                    "Alert fingerprint exists, but duplicate-skip is temporarily disabled: "
                    f"fingerprint={fingerprint}, existing_alert_event_id={existing.id}, "
                    f"existing_thread_id={existing.thread_id}, new_thread_id={thread_id}"
                )
                # NOTE(test-mode): Duplicate skip for firing alerts is intentionally disabled for
                # agent capability testing. Keep the original short-circuit logic below for easy rollback.
                #
                # if existing:
                #     logger.info(
                #         "Alert fingerprint exists, skip new investigation task: "
                #         f"fingerprint={fingerprint}, alert_event_id={existing.id}, thread_id={existing.thread_id}"
                #     )
                #     await db.execute(
                #         update(AlertEvent)
                #         .where(AlertEvent.id == existing.id)
                #         .values(
                #             status="firing",
                #             ends_at=None,
                #             labels=alert.labels,
                #             annotations=alert.annotations,
                #         )
                #     )
                #     await db.commit()
                #     continue
                await db.execute(
                    update(AlertEvent)
                    .where(AlertEvent.id == existing.id)
                    .values(
                        status="firing",
                        ends_at=None,
                        labels=alert.labels,
                        annotations=alert.annotations,
                        thread_id=thread_id,
                        session_id=trace_session.id,
                        analysis_status="pending",
                        analysis_started_at=None,
                        analysis_completed_at=None,
                        analysis_duration_sec=None,
                        log_error_warn_count=0,
                        metrics_snapshot=None,
                        log_summary=None,
                        analysis_report=None,
                    )
                )
                await db.commit()
                await write_system_audit_log(
                    user_id=AUTO_BOT_USER_ID,
                    user_role=AUTO_BOT_ROLE,
                    event_type="alert_receive",
                    status="success",
                    details={
                        "alert_event_id": existing.id,
                        "fingerprint": fingerprint,
                        "alert_name": alert_name,
                        "severity": severity,
                        "alert_status": alert.status,
                        "instance": instance,
                        "thread_id": thread_id,
                        "session_id": trace_session.id,
                        "duplicate": True,
                        "labels": alert.labels,
                        "annotations": alert.annotations,
                    },
                )

                logger.info(
                    "Reused existing alert event and scheduling investigation task: "
                    f"alert_event_id={existing.id}, thread_id={thread_id}, fingerprint={fingerprint}"
                )
                background_tasks.add_task(
                    run_agent,
                    thread_id,
                    existing.id,
                    trace_session.id,
                    bot_user.id,
                    app_loop,
                    initial_state,
                )
                continue

            # 不存在重复告警
            # 创建 thread_id, trace_session, AlertEvent
            thread_id = gen_id("thread")
            alert_context = alert.model_dump() if hasattr(alert, "model_dump") else dict(alert)
            trace_session = await _create_auto_trace_session(
                db,
                bot_user_id=bot_user.id,
                thread_id=thread_id,
                alert_name=alert_name,
                alert_context=alert_context,
            )

            # 创建初始状态
            initial_state = {
                "user_role": AUTO_BOT_ROLE,
                "mode": "auto",
                "alert_context": alert_context,
                    "evidence": [],
                    "hypotheses": [],
                    "approval_requests": [],
                    "actions_executed": [],
                    "messages": [HumanMessage(content=f"收到新告警，请开始自动排查：{alert.labels}")],
            }

            event = AlertEvent(
                fingerprint=fingerprint,
                alert_name=alert_name,
                severity=severity,
                status="firing",
                instance=instance,
                labels=alert.labels,
                annotations=alert.annotations,
                starts_at=starts_at or datetime.utcnow(),
                ends_at=None,
                thread_id=thread_id,
                session_id=trace_session.id,
                analysis_status="pending",
            )
            db.add(event)
            await db.commit()
            await db.refresh(event)
            await write_system_audit_log(
                user_id=AUTO_BOT_USER_ID,
                user_role=AUTO_BOT_ROLE,
                event_type="alert_receive",
                status="success",
                details={
                    "alert_event_id": event.id,
                    "fingerprint": fingerprint,
                    "alert_name": alert_name,
                    "severity": severity,
                    "alert_status": alert.status,
                    "instance": instance,
                    "thread_id": thread_id,
                    "session_id": trace_session.id,
                    "duplicate": False,
                    "labels": alert.labels,
                    "annotations": alert.annotations,
                },
            )

            logger.info(
                "Created new alert event and scheduling investigation task: "
                f"alert_event_id={event.id}, thread_id={thread_id}, fingerprint={fingerprint}"
            )
            background_tasks.add_task(
                run_agent,
                thread_id,
                event.id,
                trace_session.id,
                bot_user.id,
                app_loop,
                initial_state,
            )

        elif alert.status == "resolved":
            logger.info(f"Mark alert resolved: fingerprint={fingerprint}, ends_at={ends_at}")
            await db.execute(
                update(AlertEvent)
                .where(AlertEvent.fingerprint == fingerprint)
                .values(
                    status="resolved",
                    ends_at=ends_at,
                    labels=alert.labels,
                    annotations=alert.annotations,
                )
            )
            await db.commit()
            await write_system_audit_log(
                user_id=AUTO_BOT_USER_ID,
                user_role=AUTO_BOT_ROLE,
                event_type="alert_update",
                status="success",
                details={
                    "fingerprint": fingerprint,
                    "alert_name": alert_name,
                    "severity": severity,
                    "alert_status": alert.status,
                    "instance": instance,
                    "ends_at": ends_at.isoformat() if ends_at else None,
                    "labels": alert.labels,
                    "annotations": alert.annotations,
                },
            )

    return {"status": "accepted", "msg": "Investigation started in background"}
