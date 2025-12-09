import uuid, datetime, json

def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"

def gen_id(prefix="t"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

def dump_json(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)
