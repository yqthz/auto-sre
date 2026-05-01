from langchain_core.messages import SystemMessage

from app.agent.prompts.sre_system_prompt import SRE_COPILOT_SYSTEM_PROMPT
from app.agent.tools.tool_manager import get_agent_tools
from app.utils.llm_utils import get_llm

llm = get_llm()

def sre_node(state):
    messages = state["messages"]
    user_role = state.get("user_role", "viewer")

    system_msg = SystemMessage(content=SRE_COPILOT_SYSTEM_PROMPT)

    conversation = [system_msg] + messages

    tools = get_agent_tools(user_role, mode=state.get("mode", "manual"))

    llm_with_tools = llm.bind_tools(tools)

    response = llm_with_tools.invoke(conversation)

    return {"messages": [response]}