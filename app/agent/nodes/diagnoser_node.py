import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agent.prompts.diagnoser_system_prompt import DIAGNOSER_SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.tools import log_analysis_tools  # noqa: F401  # ensure tool registration side effect
from app.agent.tools import prometheus_tools  # noqa: F401  # ensure tool registration side effect
from app.agent.tools.tool_manager import get_agent_tools
from app.core.logger import logger
from app.utils.llm_utils import get_llm

llm = get_llm()
EVIDENCE_TOOLS = {"query_prometheus_metrics", "analyze_log_around_alert"}


def _safe_json_loads(raw: Any):
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _evidence_fingerprint(tool_name: str, payload: Any) -> str:
    try:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        body = str(payload)
    seed = f"{tool_name}:{body}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _collect_evidence(messages: List[Any], existing: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    evidence = list(existing)
    seen = {item.get("fingerprint") for item in evidence if isinstance(item, dict)}

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue
        if msg.name not in EVIDENCE_TOOLS:
            continue

        parsed = _safe_json_loads(msg.content)
        if parsed is None:
            parsed = {"raw": str(msg.content)}

        fp = _evidence_fingerprint(msg.name, parsed)
        if fp in seen:
            continue

        evidence.append(
            {
                "fingerprint": fp,
                "source": "tool",
                "tool": msg.name,
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "data": parsed,
            }
        )
        seen.add(fp)

    return evidence


def _collect_hypotheses(messages: List[Any], existing: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    hypotheses = list(existing)
    seen = {item.get("content") for item in hypotheses if isinstance(item, dict)}

    for msg in messages:
        if not isinstance(msg, AIMessage):
            continue
        if msg.tool_calls:
            continue

        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        content = content.strip()
        if not content:
            continue
        if content in seen:
            continue

        hypotheses.append(
            {
                "source": "diagnoser",
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "content": content[:1200],
            }
        )
        seen.add(content)

    return hypotheses


def diagnoser_node(state: AgentState):
    messages = state["messages"]
    alert_context = state.get("alert_context", {})
    alert_context_str = json.dumps(alert_context, ensure_ascii=False, indent=2)

    existing_evidence = state.get("evidence", [])
    existing_hypotheses = state.get("hypotheses", [])

    evidence = _collect_evidence(messages, existing_evidence)
    hypotheses = _collect_hypotheses(messages, existing_hypotheses)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", DIAGNOSER_SYSTEM_PROMPT),
            MessagesPlaceholder(variable_name="history"),
        ]
    )

    tools = get_agent_tools(
        user_role="viewer",
        mode="auto",
        tags=["docker", "prometheus", "log"],
    )

    logger.info(f"diagnoser selected tools count={len(tools)}")

    llm_with_tools = llm.bind_tools(tools)
    chain = prompt | llm_with_tools
    response = chain.invoke({"history": messages, "alert_info": alert_context_str})

    # If diagnoser gives a non-tool response, capture it as latest hypothesis candidate.
    hypotheses = _collect_hypotheses([response], hypotheses)

    return {
        "messages": [response],
        "evidence": evidence,
        "hypotheses": hypotheses,
    }
