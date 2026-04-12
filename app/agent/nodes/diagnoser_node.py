import json

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agent.state import AgentState
from app.agent.tools import log_analysis_tools  # noqa: F401  # ensure tool registration side effect
from app.agent.tools import prometheus_tools  # noqa: F401  # ensure tool registration side effect
from app.agent.tools.tool_manager import get_agent_tools
from app.core.logger import logger
from app.agent.prompts.diagnoser_system_prompt import DIAGNOSER_SYSTEM_PROMPT
from app.utils.llm_utils import get_llm

llm = get_llm()


def diagnoser_node(state: AgentState):
    messages = state["messages"]
    alert_context = state.get("alert_context", {})
    alert_context_str = json.dumps(alert_context, ensure_ascii=False, indent=2)

    prompt = ChatPromptTemplate.from_messages([
        ("system", DIAGNOSER_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="history"),
    ])

    tools = get_agent_tools(
        user_role="viewer",
        mode="auto",
        tags=["docker", "prometheus", "log"],
    )

    logger.info(f"diagnoser selected tools count={len(tools)}")

    llm_with_tools = llm.bind_tools(tools)
    chain = prompt | llm_with_tools
    response = chain.invoke({"history": messages, "alert_info": alert_context_str})

    return {"messages": [response]}
