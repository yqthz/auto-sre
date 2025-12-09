import json, os
from typing import Dict

from app.utils.format_utils import gen_id, now_iso

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

def save_alert(alert):
    aid = gen_id("alert")
    path = os.path.join(DATA_DIR, f"{aid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"id": aid, "ts": now_iso(), "alert": alert}, f, ensure_ascii=False)
    return aid

def save_report(report):
    rid = gen_id("report")
    path = os.path.join(DATA_DIR, f"{rid}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"id": rid, "ts": now_iso(), "report": report}, f, ensure_ascii=False)
    return rid

def append_audit(entry: Dict):
    with open(os.path.join(DATA_DIR, "audit.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
