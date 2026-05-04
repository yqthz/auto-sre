import os

import requests

from app.core.logger import logger
from app.storage import append_audit
from app.utils.format_utils import now_iso

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")

def send_dingtalk(title: str, content: str):
    """ 发送钉钉 Webhook """
    if not DINGTALK_WEBHOOK:
        logger.error("DINGTALK_WEBHOOK env variable not set")
        append_audit({
            "timestamp": now_iso(),
            "event": "notification_send",
            "user_id": "system",
            "user_role": "system",
            "status": "skipped",
            "channel": "dingtalk",
            "title": title,
            "error": "DINGTALK_WEBHOOK env variable not set",
        })
        return

    data = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text":  f"## {title}\n\n{content}"
        }
    }

    try:
        resp = requests.post(DINGTALK_WEBHOOK, json=data)
        if resp.json().get("errcode") == 0:
            logger.info("send dingtalk success")
            status = "success"
        else:
            logger.error("send dingtalk fail")
            status = "failed"
        append_audit({
            "timestamp": now_iso(),
            "event": "notification_send",
            "user_id": "system",
            "user_role": "system",
            "status": status,
            "channel": "dingtalk",
            "title": title,
            "response_status_code": resp.status_code,
            "response": resp.text,
        })
    except Exception as e:
        logger.error(f"send dingtalk error: {e}")
        append_audit({
            "timestamp": now_iso(),
            "event": "notification_send",
            "user_id": "system",
            "user_role": "system",
            "status": "failed",
            "channel": "dingtalk",
            "title": title,
            "error": str(e),
        })
