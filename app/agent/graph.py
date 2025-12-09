from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.diagnoser_node import diagnoser_node
from app.agent.reporter_node import reporter_node
from app.agent.sre_agent import sre_node
from app.agent.state import AgentState
from app.tools.security import TOOL_REGISTRY

SENSITIVE_TOOLS = ["restart_server"]
ALL_TOOLS_LIST = [meta["fn"] for meta in TOOL_REGISTRY.values()]

def entry_router(state: AgentState):
    """决定入口：是自动诊断还是人工对话"""
    if state.get("mode") == "auto":
        return "diagnoser"
    return "sre_agent"


def diagnoser_router(state: AgentState):
    """诊断者的逻辑分支：需要工具 vs 生成报告"""
    messages = state["messages"]
    last_message = messages[-1]

    if last_message.tool_calls:
        return "tools"
    return "reporter"

def post_tool_router(state: AgentState):
    """工具执行完后，回哪里去？"""
    # 根据 mode 原路返回
    if state.get("mode") == "auto":
        return "diagnoser"
    return "sre_agent"

def should_continue(state: AgentState):
    last_message = state['messages'][-1]
    if last_message.tool_calls:
        return "tools"
    return END

def create_graph():
    builder = StateGraph(AgentState)

    builder.add_node("diagnoser", diagnoser_node)
    builder.add_node("sre_agent", sre_node)
    builder.add_node("reporter", reporter_node)
    builder.add_node("tools", ToolNode(ALL_TOOLS_LIST))

    builder.set_conditional_entry_point(
        entry_router,
        {
            "diagnoser": "diagnoser",
            "sre_agent": "sre_agent"
        }
    )

    builder.add_conditional_edges(
        "diagnoser",
        diagnoser_router,
        {
            "tools": "tools",
            "reporter": "reporter"
        }
    )

    builder.add_conditional_edges(
        "sre_agent",
        should_continue,
        {
            "tools": "tools",
            END: END
        }
    )

    builder.add_conditional_edges(
        "tools",
        post_tool_router,
        {
            "diagnoser": "diagnoser",
            "sre_agent": "sre_agent"
        }
    )

    builder.add_edge("reporter", END)

    memory = MemorySaver()

    return builder.compile(checkpointer=memory, interrupt_before=["tools"])
