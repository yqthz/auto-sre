import json
import time
import uuid
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig

from app.agent.prompts.report_system_prompt import REPORTER_SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.trace_runtime import extract_usage_from_llm_response, trace_runtime
from app.core.logger import logger
from app.utils.llm_utils import get_llm

llm = get_llm()


def _run_id_from_config(config: Optional[RunnableConfig]) -> Optional[str]:
    cfg = dict(config or {})
    configurable = dict(cfg.get("configurable") or {})
    run_id = configurable.get("trace_run_id")
    return str(run_id) if run_id else None


def reporter_node(state: AgentState, config: Optional[RunnableConfig] = None):
    messages = state["messages"]

    alert_context = state.get("alert_context", {})
    evidence = state.get("evidence", [])
    hypotheses = state.get("hypotheses", [])
    approval_requests = state.get("approval_requests", [])
    actions_executed = state.get("actions_executed", [])

    alert_context_str = json.dumps(alert_context, ensure_ascii=False, indent=2)
    evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)
    hypotheses_str = json.dumps(hypotheses, ensure_ascii=False, indent=2)
    approval_requests_str = json.dumps(approval_requests, ensure_ascii=False, indent=2)
    actions_executed_str = json.dumps(actions_executed, ensure_ascii=False, indent=2)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", REPORTER_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="history"),
        ]
    )

    chain = prompt | llm

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

    response = chain.invoke(
        {
            "history": messages,
            "alert_info": alert_context_str,
            "evidence_json": evidence_str,
            "hypotheses_json": hypotheses_str,
            "approval_requests_json": approval_requests_str,
            "actions_executed_json": actions_executed_str,
        }
    )

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

    report_text = response.content if isinstance(response.content, str) else str(response.content)

    # Validate reporter output as JSON and enforce minimal schema.
    try:
        parsed = json.loads(report_text)
        if not isinstance(parsed, dict):
            raise ValueError("report JSON must be an object")

        summary = parsed.get("summary")
        root_cause = parsed.get("root_cause")
        recommendations = parsed.get("recommendations")

        if not isinstance(summary, str) or not isinstance(root_cause, str):
            raise ValueError("summary/root_cause must be strings")
        if not isinstance(recommendations, list) or not all(isinstance(x, str) for x in recommendations):
            raise ValueError("recommendations must be a string array")
        if not recommendations:
            raise ValueError("recommendations must contain at least one item")

        normalized_report = json.dumps(parsed, ensure_ascii=False)
    except Exception as e:
        logger.error(f"reporter_node produced invalid JSON report: {e}")
        fallback = {
            "summary": "insufficient information",
            "root_cause": "reporter output did not pass JSON validation, see raw_text",
            "recommendations": ["check alert context and tool outputs, then retry analysis"],
            "raw_text": report_text,
            "error": str(e),
        }
        normalized_report = json.dumps(fallback, ensure_ascii=False)

    return {
        "report": normalized_report,
        "messages": [response],
        "evidence": evidence,
        "hypotheses": hypotheses,
        "approval_requests": approval_requests,
        "actions_executed": actions_executed,
    }
