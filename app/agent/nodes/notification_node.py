from langchain_core.messages import AIMessage

from app.agent.state import AgentState
from app.notification.send_report import broadcase_report


def notification_node(state: AgentState):
    """Send diagnosis report."""
    report = state.get("report")
    alert_context = state.get("alert_context", {})
    mode = state.get("mode")

    if report and mode == "auto":
        labels = alert_context.get("labels") if isinstance(alert_context, dict) else {}
        if not isinstance(labels, dict):
            labels = {}
        alert_name = str(labels.get("alertname") or "unknown_alert")
        broadcase_report(report, alert_name)
        return {"messages": [AIMessage(content="Report has been sent to email")]} 
    return {}
