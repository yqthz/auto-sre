from app.agent.tools.loader import ensure_dispatcher_meta_tools_loaded
from app.agent.tools.security import TOOL_REGISTRY
from app.core.logger import logger

def _meta_tools_for_role(user_role: str):
    """
    Return dispatcher meta-tools allowed for the given role.
    """
    ensure_dispatcher_meta_tools_loaded()
    selected_tools = []
    for name in ["cli_list", "cli_tool_doc", "dispatch_tool"]:
        meta = TOOL_REGISTRY.get(name)
        if not meta:
            continue
        if user_role not in meta["roles"]:
            continue
        selected_tools.append(meta["fn"])
    return selected_tools


def get_agent_tools(user_role: str = "viewer", mode: str = "auto"):
    """
    Get tools exposed to the agent for the current request.

    Current behavior:
    - Expose dispatcher meta-tools only.
    - Apply role-based filtering through ``_meta_tools_for_role``.

    Args:
        user_role: Current caller role (admin/sre/viewer).
        mode: Runtime mode (manual/auto). Reserved for compatibility and
            telemetry; currently does not change tool selection.
    """
    tools = _meta_tools_for_role(user_role)
    logger.info(f"meta tools selected count={len(tools)} mode={mode}")
    return tools
