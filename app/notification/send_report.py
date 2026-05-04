from app.notification.email_notification import send_email
from app.storage import append_audit
from app.utils.format_utils import now_iso


def broadcase_report(report_content: str, alert_name: str = "系统异常"):
    """ 广播通知：同时发给所有配置的渠道 """
    title = f"故障诊断报告: {alert_name}"

    # send_email(title, report_content)
    with open("report.md", "w", encoding="utf-8") as f:
        f.write(title)
        f.write(report_content)
    append_audit({
        "timestamp": now_iso(),
        "event": "notification_send",
        "user_id": "system",
        "user_role": "system",
        "status": "success",
        "channel": "file",
        "title": title,
        "path": "report.md",
    })
