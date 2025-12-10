from langchain_core.messages import AIMessage

from app.agent.state import AgentState
from app.notification.send_report import broadcase_report


def notification_node(state: AgentState):
    """ 发送诊断报告 """
    report = state.get("report")
    alert_context = state.get("alert_context", {})
    mode = state.get("mode")

    if report and mode == "auto":
        alert_name = alert_context.labels['alertname']
        broadcase_report(report, alert_name)
        return {"messages": [AIMessage(content="报告已发送到邮箱")]}
    return {}