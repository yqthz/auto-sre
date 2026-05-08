from typing import Optional

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agent.prompts.sre_system_prompt import SRE_COPILOT_SYSTEM_PROMPT
from app.agent.trace import LLMTrace
from app.agent.tools.tool_manager import get_agent_tools
from app.utils.llm_utils import get_llm

llm = get_llm()


def _run_id_from_config(config: Optional[RunnableConfig]) -> Optional[str]:
    """从 config 中提取 run id"""
    cfg = dict(config or {})
    configurable = dict(cfg.get("configurable") or {})
    run_id = configurable.get("trace_run_id")
    return str(run_id) if run_id else None


def sre_node(state, config: Optional[RunnableConfig] = None):
    messages = state["messages"]
    user_role = state.get("user_role", "viewer")

    system_msg = SystemMessage(content=SRE_COPILOT_SYSTEM_PROMPT)
    conversation = [system_msg] + messages

    tools = get_agent_tools(user_role, mode=state.get("mode", "manual"))
    llm_with_tools = llm.bind_tools(tools)

    run_id = _run_id_from_config(config)
    current_input = ""
    if messages:
        last_msg = messages[-1]
        content = getattr(last_msg, "content", "")
        current_input = content if isinstance(content, str) else str(content)

    response = LLMTrace.invoke(
        run_id=run_id,
        node_name="sre_agent",
        model=getattr(llm, "model_name", "unknown"),
        input_preview=current_input,
        invoke_fn=lambda: llm_with_tools.invoke(conversation),
    )

    return {"messages": [response]}
