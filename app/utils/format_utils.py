import datetime
import uuid


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def gen_id(prefix="t"):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"
