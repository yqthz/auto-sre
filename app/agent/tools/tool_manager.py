import os
from typing import List

from app.agent.tools.loader import ensure_dispatcher_meta_tools_loaded, ensure_tool_modules_loaded
from app.agent.tools.security import TOOL_REGISTRY
from app.core.logger import logger

DISPATCHER_META_TOOLS = {"cli_list", "cli_tool_doc", "dispatch_tool"}


def _meta_tools_for_role(user_role: str):
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


def get_agent_tools(user_role: str = "viewer", mode: str = "auto", tags: List[str] = None):
    """
    根据上下文动态筛选工具
    :param user_role: 当前用户角色 (admin/sre/viewer)
    :param mode: 运行模式 (manual/auto)
    :param tags: 保留参数，兼容旧调用方
    """
    # Step 04 default: expose only dispatcher meta-tools in both manual/auto,
    # so model context no longer includes full tool schemas on startup.
    if os.getenv("AGENT_AUTO_USE_LEGACY_TOOLS", "0") != "1":
        tools = _meta_tools_for_role(user_role)
        logger.info(f"meta tools selected count={len(tools)} mode={mode}")
        return tools

    # Rollback path for auto mode only.
    if mode != "auto":
        return _meta_tools_for_role(user_role)

    ensure_tool_modules_loaded()
    selected_tools = []
    logger.info(f"tool registry size={len(TOOL_REGISTRY)}")

    for name, meta in TOOL_REGISTRY.items():
        if name in DISPATCHER_META_TOOLS:
            continue

        if user_role not in meta["roles"]:
            continue

        if meta["permission"] == "danger":
            continue

        if tags:
            tool_tags = set(meta.get("tags", []))
            if not tool_tags.intersection(set(tags)):
                continue

        selected_tools.append(meta["fn"])

    logger.info(f"legacy auto tools selected count={len(selected_tools)}")
    return selected_tools
