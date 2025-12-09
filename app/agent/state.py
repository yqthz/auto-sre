from typing import TypedDict, Annotated, List, Dict, Any, Literal

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    user_role: Literal["admin", "sre", "viewer"]     # 用户角色
    mode: Literal["auto", "manual"]            # 自动运行还是交互运行
    messages: Annotated[List, add_messages]    # 对话历史
    alert_context: Dict | None          # 告警上下文
    report: str | None                  # 分析报告
