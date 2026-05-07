import time
import uuid
from typing import Optional

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig

from app.agent.prompts.sre_system_prompt import SRE_COPILOT_SYSTEM_PROMPT
from app.agent.tools.tool_manager import get_agent_tools
from app.agent.trace_runtime import extract_usage_from_llm_response, trace_runtime
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
    call_id = uuid.uuid4().hex
    started = time.time()

    current_input = ""
    if messages:
        last_msg = messages[-1]
        content = getattr(last_msg, "content", "")
        current_input = content if isinstance(content, str) else str(content)

    if run_id:
        trace_runtime.append_event(
            run_id=run_id,
            event_type="llm_call_start",
            call_id=call_id,
            status="running",
            meta={"input": current_input},
        )

    response = llm_with_tools.invoke(conversation)

    if run_id:
        output = response.content if isinstance(response.content, str) else str(response.content)
        usage = extract_usage_from_llm_response(response)
        duration_ms = max(0, int((time.time() - started) * 1000))
        meta = {"output": output}
        if usage:
            meta["usage"] = usage
            trace_runtime.add_usage(run_id, usage)

        trace_runtime.append_event(
            run_id=run_id,
            event_type="llm_call_end",
            call_id=call_id,
            status="success",
            duration_ms=duration_ms,
            meta=meta,
        )

    return {"messages": [response]}
