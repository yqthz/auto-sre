from app.notification.email_notification import send_email


def broadcase_report(report_content: str, alert_name: str = "系统异常"):
    """ 广播通知：同时发给所有配置的渠道 """
    title = f"故障诊断报告: {alert_name}"

    # send_email(title, report_content)
    with open("report.md", "w", encoding="utf-8") as f:
        f.write(title)
        f.write(report_content)