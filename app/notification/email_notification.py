import os
import smtplib
from email.header import Header
from email.mime.text import MIMEText

from app.core.config import settings
from app.core.logger import logger

EMAIL_CONFIG = {
    "host": settings.SMTP_HOST,
    "port": settings.SMTP_PORT,
    "user": settings.SMTP_USER,
    "pass": settings.SMTP_PASS,
    "receiver": settings.ALERT_RECEIVER,
}

def send_email(title: str, content: str):
    """ 发送邮件 """
    if not EMAIL_CONFIG["user"]:
        return

    logger.info(f"email config: {EMAIL_CONFIG}")

    message = MIMEText(content, 'plain', 'utf-8')
    message['From'] = EMAIL_CONFIG['user']
    message['To'] = EMAIL_CONFIG['receiver']
    message['Subject'] = title

    try:
        server = smtplib.SMTP_SSL(EMAIL_CONFIG["host"], EMAIL_CONFIG["port"])
        server.login(EMAIL_CONFIG["user"], EMAIL_CONFIG["pass"])
        server.sendmail(EMAIL_CONFIG["user"], EMAIL_CONFIG["receiver"], message.as_string())
        server.quit()
        logger.info("send email success")
    except Exception as e:
        logger.error(f"send email fail {e}")
