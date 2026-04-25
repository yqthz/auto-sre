import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import create_graph
from app.agent.tools.security import after_tool_execution, before_tool_execution, is_sensitive_tool
from app.api import deps
from app.core.logger import logger
from app.db.session import AsyncSessionLocal
from app.model.alert_event import AlertEvent
from app.schema.alert_info import WebhookPayload
from app.utils.format_utils import gen_id

router = APIRouter()
graph = create_graph()

AUTO_BOT_USER_ID = "system_autobot"
AUTO_BOT_ROLE = "viewer"


def _safe_json_loads(raw: Any):
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_alert_time(raw: Optional[str]) -> Optional[datetime]:
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
    async with AsyncSessionLocal() as db:
        await db.execute(
            update(AlertEvent)
            .where(AlertEvent.id == alert_event_id)
            .values(analysis_status=status)
        )
        await db.commit()


async def save_analysis_results(thread_id: str, alert_event_id: int, analysis_status: str):
    async with AsyncSessionLocal() as db:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = graph.get_state(config)
        messages = snapshot.values.get("messages", [])
        report_text = snapshot.values.get("report", "")

        metrics_snapshot = None
        log_summary = None

        for msg in messages:
            if isinstance(msg, ToolMessage):
                if msg.name == "query_prometheus_metrics":
                    metrics_snapshot = _safe_json_loads(msg.content)
                elif msg.name == "analyze_log_around_alert":
                    log_summary = _safe_json_loads(msg.content)

        analysis_report = _safe_json_loads(report_text)

        await db.execute(
            update(AlertEvent)
            .where(AlertEvent.id == alert_event_id)
            .values(
                metrics_snapshot=metrics_snapshot,
                log_summary=log_summary,
                analysis_report=analysis_report,
                analysis_status=analysis_status,
            )
        )
        await db.commit()


def run_agent(
    thread_id: str,
    alert_event_id: int,
    app_loop: asyncio.AbstractEventLoop,
    initial_input: Optional[Dict] = None,
):
    """
    鍚庡彴浠诲姟锛氶┍锟?Agent 鑷姩杩愯锟?    """
    config = {"configurable": {"thread_id": thread_id}}
    current_input = initial_input
    success = True

    logger.info(f"Thread {thread_id}: Starting loop")
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
                        after_tool_execution(
                            tool_name=msg.name,
                            result=msg.content,
                            user_id=AUTO_BOT_USER_ID,
                            user_role=AUTO_BOT_ROLE,
                        )
                        logger.info(f"Tool {msg.name} executed. Result len: {len(msg.content)}")

            snapshot = graph.get_state(config)
            if not snapshot.next:
                logger.info(f"Thread {thread_id}: Process finished successfully.")
                break

            if snapshot.next[0] == "tools":
                last_message = snapshot.values["messages"][-1]
                if isinstance(last_message, AIMessage) and last_message.tool_calls:
                    tool_calls = last_message.tool_calls

                    all_checks_passed = True
                    for tc in tool_calls:
                        try:
                            before_tool_execution(
                                tool_name=tc["name"],
                                args=tc["args"],
                                user_id=AUTO_BOT_USER_ID,
                                user_role=AUTO_BOT_ROLE,
                                mode="auto",
                            )
                        except PermissionError as e:
                            logger.error(f"Security check failed for {tc['name']}: {e}")

                            deny_msg = ToolMessage(
                                tool_call_id=tc["id"],
                                content=f"SecurityError: {str(e)}",
                                name=tc["name"],
                            )
                            graph.update_state(config, {"messages": [deny_msg]}, as_node="tools")
                            all_checks_passed = False
                            break

                    if not all_checks_passed:
                        current_input = None
                        continue

                    tool_names = [t["name"] for t in tool_calls]
                    has_sensitive_tool = any(is_sensitive_tool(name) for name in tool_names)

                    if has_sensitive_tool:
                        logger.warning(f"Thread {thread_id}: Paused for sensitive tools: {tool_names}")
                        success = False
                        break

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
    asyncio.run_coroutine_threadsafe(
        save_analysis_results(thread_id, alert_event_id, final_status), app_loop
    ).result()


@router.post("/alertmanager")
async def receive_alert(
    request: WebhookPayload,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(deps.get_db),
):
    """
    鎺ユ敹 Alertmanager 鍛婅锛岀珛鍗宠繑鍥烇紝鍚庡彴澶勭悊锟?    """
    alerts = request.alerts
    logger.info(f"Received {len(alerts)} alerts")

    app_loop = asyncio.get_running_loop()

    for alert in alerts:
        fingerprint = _build_fingerprint(alert)
        starts_at = _parse_alert_time(alert.startsAt)
        ends_at = _parse_alert_time(alert.endsAt)
        alert_name = alert.labels.get("alertname", "unknown")
        severity = alert.labels.get("severity", "unknown")
        instance = alert.labels.get("instance")

        if alert.status == "firing":
            stmt = select(AlertEvent).where(AlertEvent.fingerprint == fingerprint)
            result = await db.execute(stmt)
            existing = result.scalars().first()

            if existing:
                thread_id = gen_id("thread")
                alert_context = alert.model_dump() if hasattr(alert, "model_dump") else dict(alert)
                initial_state = {
                "user_role": AUTO_BOT_ROLE,
                "mode": "auto",
                "alert_context": alert_context,
                "evidence": [],
                "hypotheses": [],
                "approval_requests": [],
                "actions_executed": [],
                "messages": [HumanMessage(content=f"閺€璺哄煂閺傛澘鎲＄拃锔肩礉鐠囧嘲绱戞慨瀣殰閸斻劍甯撻弻? {alert.labels}")],
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
                        analysis_status="pending",
                        metrics_snapshot=None,
                        log_summary=None,
                        analysis_report=None,
                    )
                )
                await db.commit()

                logger.info(
                    "Reused existing alert event and scheduling investigation task: "
                    f"alert_event_id={existing.id}, thread_id={thread_id}, fingerprint={fingerprint}"
                )
                background_tasks.add_task(run_agent, thread_id, existing.id, app_loop, initial_state)
                continue

            thread_id = gen_id("thread")
            alert_context = alert.model_dump() if hasattr(alert, "model_dump") else dict(alert)
            initial_state = {
                "user_role": AUTO_BOT_ROLE,
                "mode": "auto",
                "alert_context": alert_context,
                    "evidence": [],
                    "hypotheses": [],
                    "approval_requests": [],
                    "actions_executed": [],
                    "messages": [HumanMessage(content=f"鏀跺埌鏂板憡璀︼紝璇峰紑濮嬭嚜鍔ㄦ帓锟? {alert.labels}")],
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
                analysis_status="pending",
            )
            db.add(event)
            await db.commit()
            await db.refresh(event)

            logger.info(
                "Created new alert event and scheduling investigation task: "
                f"alert_event_id={event.id}, thread_id={thread_id}, fingerprint={fingerprint}"
            )
            background_tasks.add_task(run_agent, thread_id, event.id, app_loop, initial_state)

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

    return {"status": "accepted", "msg": "Investigation started in background"}



