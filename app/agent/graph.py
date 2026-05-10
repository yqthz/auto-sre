from langgraph.checkpoint.memory import MemorySaver
from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.nodes.diagnoser_node import diagnoser_node
from app.agent.nodes.notification_node import notification_node
from app.agent.nodes.sre_agent import sre_node
from app.agent.state import AgentState
from app.agent.tools.loader import ensure_tool_modules_loaded
from app.agent.tools.security import TOOL_REGISTRY


def _all_tools_list():
    ensure_tool_modules_loaded()
    return [meta["fn"] for meta in TOOL_REGISTRY.values()]


def entry_router(state: AgentState):
    """Decide whether to start with auto diagnosis or manual chat."""
    if state.get("mode") == "auto":
        return "diagnoser"
    return "sre_agent"


def diagnoser_router(state: AgentState):
    """Route diagnoser output to tools or notification."""
    messages = state["messages"]
    last_message = messages[-1]

    if last_message.tool_calls:
        return "tools"
    return "notification"


def post_tool_router(state: AgentState):
    """Route after tool execution."""
    if state.get("mode") == "auto":
        return "diagnoser"
    return "sre_agent"


def should_continue(state: AgentState):
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        return "tools"
    return END


def create_graph():
    builder = StateGraph(AgentState)

    builder.add_node("diagnoser", diagnoser_node)
    builder.add_node("sre_agent", sre_node)
    builder.add_node("notification", notification_node)
    builder.add_node("tools", ToolNode(_all_tools_list()))

    builder.set_conditional_entry_point(
        entry_router,
        {
            "diagnoser": "diagnoser",
            "sre_agent": "sre_agent",
        },
    )

    builder.add_conditional_edges(
        "diagnoser",
        diagnoser_router,
        {
            "tools": "tools",
            "notification": "notification",
        },
    )

    builder.add_conditional_edges(
        "sre_agent",
        should_continue,
        {
            "tools": "tools",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "tools",
        post_tool_router,
        {
            "diagnoser": "diagnoser",
            "sre_agent": "sre_agent",
        },
    )

    builder.add_edge("notification", END)

    memory = MemorySaver()

    return builder.compile(checkpointer=memory, interrupt_before=["tools"])
