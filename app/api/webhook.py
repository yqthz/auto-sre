from typing import Optional, Dict

from fastapi import APIRouter, BackgroundTasks
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage

from app.agent.graph import create_graph, SENSITIVE_TOOLS
from app.agent.tools.security import after_tool_execution, before_tool_execution
from app.core.logger import logger
from app.schema.alert_info import WebhookPayload
from app.utils.format_utils import gen_id

router = APIRouter()
graph = create_graph()

AUTO_BOT_USER_ID = "system_autobot"
AUTO_BOT_ROLE = "viewer"

def run_agent(thread_id: str, initial_input: Optional[Dict] = None):
    """
    后台任务：驱动 Agent 自动运行
    """
    config = {"configurable": {"thread_id": thread_id}}

    current_input = initial_input

    logger.info(f"Thread {thread_id}: Starting loop")

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
                    # 获取 ai 想调用的所有工具
                    tool_calls = last_message.tool_calls

                    all_checks_passed = True
                    for tc in tool_calls:
                        try:
                            before_tool_execution(
                                tool_name=tc["name"],
                                args=tc["args"],
                                user_id=AUTO_BOT_USER_ID,
                                user_role=AUTO_BOT_ROLE
                            )
                        except PermissionError as e:
                            logger.error(f"Security check failed for {tc['name']}: {e}")

                            deny_msg = ToolMessage(
                                tool_call_id=tc['id'],
                                content=f"SecurityError: {str(e)}",
                                name=tc['name']
                            )
                            graph.update_state(config, {"messages": [deny_msg]}, as_node="tools")
                            all_checks_passed = False
                            break

                    if not all_checks_passed:
                        current_input = None
                        continue

                    tool_names = [t["name"] for t in tool_calls]

                    # 检查是否有任何一个工具是敏感的
                    has_sensitive_tool = any(name in SENSITIVE_TOOLS for name in tool_names)

                    if has_sensitive_tool:
                        # 遇到敏感工具：停止循环，不做任何操作。
                        logger.warning(f"Thread {thread_id}: Paused for sensitive tools: {tool_names}")
                        break
                    else:
                        # 全是安全工具：自动批准
                        logger.info(f"Thread {thread_id}: Auto-approving tools: {tool_names}")
                        current_input = None
                        continue  # 进入下一次 while 循环，继续执行 stream
                else:
                    logger.error(f"Thread {thread_id}: Stopped at tools but no calls found.")
                    break
            else:
                break
        except Exception as e:
            logger.error(f"Thread {thread_id}: Error in execution loop - {e}", exc_info=True)
            break


@router.post("/alertmanager")
async def receive_alert(request: WebhookPayload, background_tasks: BackgroundTasks):
    """
    接收 Alertmanager 告警，立即返回，后台处理。
    """
    alerts = request.alerts
    logger.info(f"Received {len(alerts)} alerts")

    for alert in alerts:
        if alert.status == "firing":

            thread_id = gen_id("thread")
            initial_state = {
                "user_role": AUTO_BOT_ROLE,
                "mode": "auto",
                "alert_context": alert,
                "messages": [HumanMessage(content=f"收到新告警，请开始自动排查: {alert.labels}")]
            }

            background_tasks.add_task(run_agent, thread_id, initial_state)

    return {"status": "accepted", "msg": "Investigation started in background"}
