import os

import requests

from app.core.logger import logger
from app.storage import append_audit
from app.utils.format_utils import now_iso

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

def send_feishu(title: str, content: str):
    """ 发送飞书 """
    if not FEISHU_WEBHOOK:
        logger.error("FEISHU_WEBHOOK env variable is not set")
        append_audit({
            "timestamp": now_iso(),
            "event": "notification_send",
            "user_id": "system",
            "user_role": "system",
            "status": "skipped",
            "channel": "feishu",
            "title": title,
            "error": "FEISHU_WEBHOOK env variable is not set",
        })
        return

    data = {
        "msg_type": "text",
        "content": {
            "text": f"【{title}】\n{content}"
        }
    }

    try:
        resp = requests.post(url=FEISHU_WEBHOOK, json=data)
        logger.info(f"feishu response {resp.json()}")
        append_audit({
            "timestamp": now_iso(),
            "event": "notification_send",
            "user_id": "system",
            "user_role": "system",
            "status": "success" if resp.ok else "failed",
            "channel": "feishu",
            "title": title,
            "response_status_code": resp.status_code,
            "response": resp.text,
        })
    except Exception as e:
        logger.error(f"send feishu error {e}")
        append_audit({
            "timestamp": now_iso(),
            "event": "notification_send",
            "user_id": "system",
            "user_role": "system",
            "status": "failed",
            "channel": "feishu",
            "title": title,
            "error": str(e),
        })
