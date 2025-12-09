from typing import List

from app.tools.security import TOOL_REGISTRY


def get_agent_tools(user_role: str = "viewer", mode: str = "auto", tags: List[str] = None):
    """
    根据上下文动态筛选工具
    :param user_role: 当前用户角色 (admin/sre/viewer)
    :param mode: 运行模式 (manual: 全功能 / auto: 自动排障，可能限制高危操作)
    :param tags: 如果只想获取特定类型的工具 (如只取 ['docker'])
    """
    selected_tools = []

    for name, meta in TOOL_REGISTRY.items():
        if user_role not in meta["roles"]:
            continue

        # 如果是 auto 模式，且工具是 danger 级别，我们不传给 LLM
        if mode == "auto" and meta["permission"] == "danger":
            continue

        if tags:
            tool_tags = set(meta.get("tags", []))
            if not tool_tags.intersection(set(tags)):
                continue

        selected_tools.append(meta["fn"])
    return selected_tools
