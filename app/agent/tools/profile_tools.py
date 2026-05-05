import json

from app.agent.runtime_profile import profile_summary
from app.agent.tools.security import register_tool


@register_tool(
    name="lookup_runtime_profile",
    permission="info",
    roles=["admin", "sre", "viewer"],
    tags=["profile"],
)
def lookup_runtime_profile():
    """Return the runtime profile used by diagnostic tools."""
    return json.dumps({"ok": True, "profile": profile_summary()}, ensure_ascii=False)
