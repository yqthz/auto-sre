import json

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agent.state import AgentState
from app.prompts.report_system_prompt import REPORTER_SYSTEM_PROMPT
from app.utils.llm_utils import get_llm

llm = get_llm()

def reporter_node(state: AgentState):
    messages = state["messages"]

    alert_context = str(state.get("alert_context", {}))
    alert_context_str = json.dumps(alert_context, ensure_ascii=False, indent=2)


    prompt = ChatPromptTemplate.from_messages([
        ("system", REPORTER_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="history"),
    ])

    chain = prompt | llm

    response = chain.invoke({"history": messages, "alert_info": alert_context_str})

    return {
        "report": response.content,
        "messages": [response]
    }

