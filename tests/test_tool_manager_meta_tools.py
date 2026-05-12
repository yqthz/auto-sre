import unittest

from langchain_core.messages import AIMessage
from langgraph.constants import END
from langgraph.graph import StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.state import AgentState
from app.agent.tools.loader import ensure_tool_modules_loaded
from app.agent.tools.security import TOOL_REGISTRY
from app.agent.tools.tool_manager import get_agent_tools


class TestToolManagerMetaTools(unittest.TestCase):
    def test_viewer_can_get_dispatcher_meta_tools(self):
        tools = get_agent_tools(user_role="viewer", mode="manual")
        names = {getattr(t, "__name__", "") for t in tools}
        self.assertIn("cli_list", names)
        self.assertIn("cli_action_doc", names)
        self.assertIn("dispatch_tool", names)

    def test_tool_node_cli_list_uses_graph_state_role_and_mode(self):
        ensure_tool_modules_loaded()
        builder = StateGraph(AgentState)
        builder.add_node("tools", ToolNode([TOOL_REGISTRY["cli_list"]["fn"]]))
        builder.set_entry_point("tools")
        builder.add_edge("tools", END)
        graph = builder.compile()

        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "cli_list",
                    "args": {},
                    "id": "call_cli_list",
                    "type": "tool_call",
                }
            ],
        )

        result = graph.invoke(
            {
                "messages": [message],
                "user_role": "admin",
                "mode": "manual",
            },
            config={"configurable": {"thread_id": "test-thread"}},
        )

        content = result["messages"][-1].content
        self.assertIn("docker.docker_restart_container", content)


if __name__ == "__main__":
    unittest.main()
