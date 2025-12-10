import json

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agent.state import AgentState
from app.core.logger import logger
from app.prompts.diagnoser_system_prompt import DIAGNOSER_SYSTEM_PROMPT
from app.tools.tool_manager import get_agent_tools
from app.utils.llm_utils import get_llm

llm = get_llm()

def diagnoser_node(state: AgentState):
    messages = state["messages"]
    alert_context = str(state.get("alert_context", {}))
    alert_context_str = json.dumps(alert_context, ensure_ascii=False, indent=2)


    prompt = ChatPromptTemplate.from_messages([
        ("system", DIAGNOSER_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="history"),
    ])

    tools = get_agent_tools(
        user_role="viewer",
        mode="auto",
        tags=["docker"]
    )

    logger.info(f"tools: {tools}")

    llm_with_tools = llm.bind_tools(tools)

    chain = prompt | llm_with_tools

    response = chain.invoke({"history": messages, "alert_info": alert_context_str})

    return {"messages": [response]}

