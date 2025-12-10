import os

import requests

from app.core.logger import logger

DINGTALK_WEBHOOK = os.getenv("DINGTALK_WEBHOOK")

def send_dingtalk(title: str, content: str):
    """ 发送钉钉 Webhook """
    if not DINGTALK_WEBHOOK:
        logger.error("DINGTALK_WEBHOOK env variable not set")
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
        else:
            logger.error("send dingtalk fail")
    except Exception as e:
        logger.error(f"send dingtalk error: {e}")