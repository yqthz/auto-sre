import json
from datetime import datetime
from typing import Optional

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig

from app.agent.prompts.report_system_prompt import REPORTER_SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.trace import LLMTrace
from app.core.logger import logger
from app.utils.llm_utils import get_llm

llm = get_llm()

ALLOWED_SEVERITIES = {"critical", "high", "medium", "low"}
ALLOWED_TIMELINE_SOURCES = {"metric", "log"}


def _run_id_from_config(config: Optional[RunnableConfig]) -> Optional[str]:
    cfg = dict(config or {})
    configurable = dict(cfg.get("configurable") or {})
    run_id = configurable.get("trace_run_id")
    return str(run_id) if run_id else None


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_iso8601(value: str) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
        return True
    except ValueError:
        return False


def _extract_json_object_text(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return raw
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[-1].strip().startswith("```"):
            body = "\n".join(lines[1:-1]).strip()
            if body.lower().startswith("json"):
                body = body[4:].lstrip()
            raw = body
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


def reporter_node(state: AgentState, config: Optional[RunnableConfig] = None):
    messages = state.get("messages", [])

    alert_context = state.get("alert_context", {})
    evidence = state.get("evidence", [])
    hypotheses = state.get("hypotheses", [])
    timeline_candidates = state.get("timeline_candidates", [])
    root_cause_candidates = state.get("root_cause_candidates", [])
    approval_requests = state.get("approval_requests", [])
    actions_executed = state.get("actions_executed", [])

    alert_context_str = json.dumps(alert_context, ensure_ascii=False, indent=2)
    evidence_str = json.dumps(evidence, ensure_ascii=False, indent=2)
    hypotheses_str = json.dumps(hypotheses, ensure_ascii=False, indent=2)
    timeline_candidates_str = json.dumps(timeline_candidates, ensure_ascii=False, indent=2)
    root_cause_candidates_str = json.dumps(root_cause_candidates, ensure_ascii=False, indent=2)
    approval_requests_str = json.dumps(approval_requests, ensure_ascii=False, indent=2)
    actions_executed_str = json.dumps(actions_executed, ensure_ascii=False, indent=2)

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", REPORTER_SYSTEM_PROMPT),
        ]
    )

    chain = prompt | llm

    run_id = _run_id_from_config(config)
    current_input = ""
    if messages:
        last_msg = messages[-1]
        content = getattr(last_msg, "content", "")
        current_input = content if isinstance(content, str) else str(content)

    response = LLMTrace.invoke(
        run_id=run_id,
        node_name="reporter",
        model=getattr(llm, "model_name", "unknown"),
        input_preview=current_input,
        invoke_fn=lambda: chain.invoke(
            {
                "alert_info": alert_context_str,
                "evidence_json": evidence_str,
                "hypotheses_json": hypotheses_str,
                "timeline_candidates_json": timeline_candidates_str,
                "root_cause_candidates_json": root_cause_candidates_str,
                "approval_requests_json": approval_requests_str,
                "actions_executed_json": actions_executed_str,
            }
        ),
    )

    report_text = response.content if isinstance(response.content, str) else str(response.content)
    report_json_text = _extract_json_object_text(report_text)

    # Validate reporter output as JSON and enforce schema.
    try:
        parsed = json.loads(report_json_text)
        if not isinstance(parsed, dict):
            raise ValueError("report JSON must be an object")

        summary = parsed.get("summary")
        severity = parsed.get("severity")
        impact_scope = parsed.get("impact_scope")
        timeline = parsed.get("timeline")
        root_causes = parsed.get("root_causes")
        recommendations = parsed.get("recommendations")
        runbook_refs = parsed.get("runbook_refs")
        risk_notes = parsed.get("risk_notes")

        if not _is_non_empty_string(summary):
            raise ValueError("summary must be a non-empty string")
        if severity not in ALLOWED_SEVERITIES:
            raise ValueError("severity must be one of critical/high/medium/low")
        if not _is_non_empty_string(impact_scope):
            raise ValueError("impact_scope must be a non-empty string")

        if not isinstance(timeline, list) or not timeline:
            raise ValueError("timeline must be a non-empty array")
        prev_time = None
        for item in timeline:
            if not isinstance(item, dict):
                raise ValueError("each timeline item must be an object")
            t = item.get("time")
            src = item.get("source")
            event = item.get("event")
            evidence_ref = item.get("evidence_ref")
            if not _is_non_empty_string(t) or not _is_iso8601(t):
                raise ValueError("timeline.time must be a valid ISO-8601 string")
            if src not in ALLOWED_TIMELINE_SOURCES:
                raise ValueError("timeline.source must be metric or log")
            if not _is_non_empty_string(event):
                raise ValueError("timeline.event must be a non-empty string")
            if not _is_non_empty_string(evidence_ref):
                raise ValueError("timeline.evidence_ref must be a non-empty string")

            current_time = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if prev_time and current_time < prev_time:
                raise ValueError("timeline must be sorted by time ascending")
            prev_time = current_time

        if not isinstance(root_causes, list) or not root_causes:
            raise ValueError("root_causes must be a non-empty array")
        for item in root_causes:
            if not isinstance(item, dict):
                raise ValueError("each root_causes item must be an object")
            hypothesis = item.get("hypothesis")
            confidence = item.get("confidence")
            evidence_refs = item.get("evidence_refs")
            if not _is_non_empty_string(hypothesis):
                raise ValueError("root_causes.hypothesis must be a non-empty string")
            if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
                raise ValueError("root_causes.confidence must be a number in [0, 1]")
            if not isinstance(evidence_refs, list) or not evidence_refs:
                raise ValueError("root_causes.evidence_refs must be a non-empty string array")
            if not all(_is_non_empty_string(x) for x in evidence_refs):
                raise ValueError("root_causes.evidence_refs must contain non-empty strings")

        if not isinstance(recommendations, list) or not recommendations:
            raise ValueError("recommendations must be a non-empty string array")
        if not all(_is_non_empty_string(x) for x in recommendations):
            raise ValueError("recommendations must contain non-empty strings")

        if not isinstance(runbook_refs, list):
            raise ValueError("runbook_refs must be a string array")
        if not all(_is_non_empty_string(x) for x in runbook_refs):
            raise ValueError("runbook_refs must contain non-empty strings")

        if not isinstance(risk_notes, str):
            raise ValueError("risk_notes must be a string")

        normalized_report = json.dumps(parsed, ensure_ascii=False)
    except Exception as e:
        logger.error(f"reporter_node produced invalid JSON report: {e}")
        fallback = {
            "summary": "insufficient information",
            "severity": "medium",
            "impact_scope": "unknown",
            "timeline": [
                {
                    "time": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "source": "log",
                    "event": "reporter output did not pass JSON validation",
                    "evidence_ref": "reporter_node_validation",
                }
            ],
            "root_causes": [
                {
                    "hypothesis": "evidence is insufficient; reporter output did not pass JSON validation",
                    "confidence": 0.1,
                    "evidence_refs": ["raw_text"],
                }
            ],
            "recommendations": [
                "If report generation fails validation, inspect alert context and tool outputs, then retry report generation within 10 minutes and verify schema compliance is restored to 100%."
            ],
            "runbook_refs": [],
            "risk_notes": "low confidence due to invalid report schema",
            "raw_text": report_text,
            "error": str(e),
        }
        normalized_report = json.dumps(fallback, ensure_ascii=False)

    return {
        "report": normalized_report,
        "messages": [response],
        "evidence": evidence,
        "hypotheses": hypotheses,
        "timeline_candidates": timeline_candidates,
        "root_cause_candidates": root_cause_candidates,
        "approval_requests": approval_requests,
        "actions_executed": actions_executed,
    }
