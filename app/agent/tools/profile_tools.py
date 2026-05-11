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
    """
    返回诊断工具当前使用的运行时配置快照。

    功能解释:
    - 输出工具执行所依赖的基础环境信息摘要。
    - 便于在排障前确认“当前工具到底连的是哪个环境”。
    - 适合作为诊断前的环境核对入口。

    使用场景:
    - 排障前做环境核对。
    - 发现工具结果与预期不符时，确认 profile 配置是否正确。

    参数说明:
    - 无。

    必填字段:
    - 无。

    调用方法:
    - `lookup_runtime_profile()`

    返回关键字段:
    - `ok`：是否成功。
    - `profile`：运行时 profile 摘要。
    """
    return json.dumps({"ok": True, "profile": profile_summary()}, ensure_ascii=False)
