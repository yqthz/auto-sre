import os

import requests

from app.core.logger import logger

FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK")

def send_feishu(title: str, content: str):
    """ 发送飞书 """
    if not FEISHU_WEBHOOK:
        logger.error("FEISHU_WEBHOOK env variable is not set")
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
    except Exception as e:
        logger.error(f"send feishu error {e}")