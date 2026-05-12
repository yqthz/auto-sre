from functools import wraps
from typing import Any, Dict, Optional

from app.agent.tools.loader import ensure_dispatcher_meta_tools_loaded
from app.agent.tools.security import TOOL_REGISTRY
from app.core.logger import logger
from langchain_core.runnables import RunnableConfig


# def _merge_meta_tool_config(
#     config: Optional[RunnableConfig],
#     *,
#     user_role: str,
#     mode: str,
# ) -> dict:
#     cfg = dict(config or {})
#     configurable = dict(cfg.get("configurable") or {})
#     configurable.setdefault("user_role", user_role)
#     configurable.setdefault("mode", mode)
#     cfg["configurable"] = configurable
#     return cfg


# def _bind_meta_tool_context(meta: dict, *, user_role: str, mode: str):
#     fn = meta["fn"]

#     if meta["name"] == "cli_list":
#         @wraps(fn)
#         def wrapped(config: Optional[RunnableConfig] = None):
#             return fn(config=_merge_meta_tool_config(config, user_role=user_role, mode=mode))

#         return wrapped

#     if meta["name"] == "cli_action_doc":
#         @wraps(fn)
#         def wrapped(action: str, config: Optional[RunnableConfig] = None):
#             return fn(action=action, config=_merge_meta_tool_config(config, user_role=user_role, mode=mode))

#         return wrapped

#     if meta["name"] == "dispatch_tool":
#         @wraps(fn)
#         def wrapped(action: str, params: Dict[str, Any], config: Optional[RunnableConfig] = None):
#             return fn(
#                 action=action,
#                 params=params,
#                 config=_merge_meta_tool_config(config, user_role=user_role, mode=mode),
#             )

#         return wrapped

#     return fn

def _meta_tools_for_role(user_role: str, mode: str):
    """
    Return dispatcher meta-tools allowed for the given role.
    """
    ensure_dispatcher_meta_tools_loaded()
    selected_tools = []
    for name in ["cli_list", "cli_action_doc", "dispatch_tool"]:
        meta = TOOL_REGISTRY.get(name)
        if not meta:
            continue
        if user_role not in meta["roles"]:
            continue
        # selected_tools.append(_bind_meta_tool_context(meta, user_role=user_role, mode=mode))
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
    tools = _meta_tools_for_role(user_role, mode=mode)
    logger.info(f"meta tools selected count={len(tools)} mode={mode} user_role={user_role}")
    return tools
