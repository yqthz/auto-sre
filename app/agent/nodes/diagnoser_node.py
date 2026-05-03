import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables import RunnableConfig

from app.agent.prompts.diagnoser_system_prompt import DIAGNOSER_SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.tools.tool_manager import get_agent_tools
from app.agent.trace_runtime import extract_usage_from_llm_response, trace_runtime
from app.core.logger import logger
from app.utils.llm_utils import get_llm

llm = get_llm()
EVIDENCE_ACTIONS = {
    "prometheus.query_prometheus_metrics",
    "prometheus.query_prometheus_range_metrics",
    "log.analyze_log_around_alert",
}
LEGACY_EVIDENCE_TOOLS = {
    "query_prometheus_metrics": "prometheus.query_prometheus_metrics",
    "query_prometheus_range_metrics": "prometheus.query_prometheus_range_metrics",
    "analyze_log_around_alert": "log.analyze_log_around_alert",
}


def _run_id_from_config(config: Optional[RunnableConfig]) -> Optional[str]:
    cfg = dict(config or {})
    configurable = dict(cfg.get("configurable") or {})
    run_id = configurable.get("trace_run_id")
    return str(run_id) if run_id else None


def _safe_json_loads(raw: Any):
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _dt_to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _evidence_fingerprint(tool_name: str, payload: Any) -> str:
    try:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    except Exception:
        body = str(payload)
    seed = f"{tool_name}:{body}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _extract_evidence_from_dispatch(msg: ToolMessage) -> Optional[tuple[str, Any]]:
    parsed = _safe_json_loads(msg.content)
    if not isinstance(parsed, dict):
        return None

    action = parsed.get("action")
    status = parsed.get("status")
    if not isinstance(action, str) or action not in EVIDENCE_ACTIONS:
        return None
    if status != "executed":
        return None

    return action, parsed.get("result")


def _extract_log_timeline(payload: Dict[str, Any], fallback_time: str) -> List[Dict[str, Any]]:
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []

    base_dt = _parse_iso(((payload.get("time_range") or {}).get("from")))
    timeline: List[Dict[str, Any]] = []

    for item in entries:
        if not isinstance(item, dict):
            continue
        event_time = str(item.get("time") or "").strip()
        level = str(item.get("level") or "").strip()
        message = str(item.get("message") or "").strip()
        count = item.get("count")

        if not message:
            continue

        iso_time = fallback_time
        if base_dt and event_time:
            parts = event_time.split(":")
            if len(parts) == 3 and all(p.isdigit() for p in parts):
                hh, mm, ss = (int(parts[0]), int(parts[1]), int(parts[2]))
                try:
                    merged = base_dt.replace(hour=hh, minute=mm, second=ss, microsecond=0)
                    iso_time = _dt_to_iso_z(merged)
                except ValueError:
                    pass

        timeline.append(
            {
                "time": iso_time,
                "source": "log",
                "event": f"{level} {message} x{count}",
                "evidence_ref": "log.analyze_log_around_alert",
                "tool": "log.analyze_log_around_alert",
            }
        )

    return timeline


def _extract_metric_snapshot_timeline(payload: Dict[str, Any], fallback_time: str) -> List[Dict[str, Any]]:
    queried_at = _parse_iso(payload.get("queried_at"))
    event_time = _dt_to_iso_z(queried_at) if queried_at else fallback_time

    metrics = payload.get("metrics")
    if not isinstance(metrics, list):
        return []

    timeline: List[Dict[str, Any]] = []
    for item in metrics[:6]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "metric")
        value = item.get("value")
        unit = str(item.get("unit") or "")
        timeline.append(
            {
                "time": event_time,
                "source": "metric",
                "event": f"{name}={value}{unit}",
                "evidence_ref": f"metrics: {name}",
                "tool": "prometheus.query_prometheus_metrics",
            }
        )
    return timeline


def _extract_metric_range_timeline(payload: Dict[str, Any], fallback_time: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    series_items = payload.get("series")
    if not isinstance(series_items, list):
        return out

    for metric_item in series_items[:4]:
        if not isinstance(metric_item, dict):
            continue
        name = str(metric_item.get("name") or "metric")
        series = metric_item.get("series")
        if not isinstance(series, list):
            continue

        best_time: Optional[str] = None
        best_value: Optional[float] = None

        for one_series in series:
            if not isinstance(one_series, dict):
                continue
            points = one_series.get("points")
            if not isinstance(points, list):
                continue
            for pair in points:
                if not isinstance(pair, list) or len(pair) < 2:
                    continue
                ts = _safe_float(pair[0])
                val = _safe_float(pair[1])
                if ts is None or val is None:
                    continue
                if best_value is None or val > best_value:
                    best_value = val
                    best_time = _dt_to_iso_z(datetime.fromtimestamp(ts, tz=timezone.utc))

        if best_time is None:
            best_time = fallback_time

        out.append(
            {
                "time": best_time,
                "source": "metric",
                "event": f"{name} peak={best_value}",
                "evidence_ref": f"metrics_range: {name}",
                "tool": "prometheus.query_prometheus_range_metrics",
            }
        )

    return out


def _extract_root_cause_hints(action: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    hints: List[Dict[str, Any]] = []

    if action == "log.analyze_log_around_alert":
        entries = payload.get("entries")
        if isinstance(entries, list) and entries:
            top = entries[0] if isinstance(entries[0], dict) else {}
            message = str(top.get("message") or "log anomaly around alert window")
            hints.append(
                {
                    "hypothesis": f"log anomaly detected: {message}",
                    "confidence": 0.45,
                    "evidence_refs": ["log.analyze_log_around_alert:entries"],
                    "tool_refs": ["log.analyze_log_around_alert"],
                }
            )

    if action == "prometheus.query_prometheus_metrics":
        metrics = payload.get("metrics")
        if isinstance(metrics, list):
            for item in metrics:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "")
                value = _safe_float(item.get("value"))
                if value is None:
                    continue
                if name == "error_rate" and value > 0.05:
                    hints.append(
                        {
                            "hypothesis": "elevated 5xx error rate indicates application instability",
                            "confidence": 0.7,
                            "evidence_refs": [f"metrics: {name}={value}"],
                            "tool_refs": ["prometheus.query_prometheus_metrics"],
                        }
                    )
                if name == "cpu_usage" and value > 85:
                    hints.append(
                        {
                            "hypothesis": "high CPU usage may cause service latency or timeout",
                            "confidence": 0.62,
                            "evidence_refs": [f"metrics: {name}={value}"],
                            "tool_refs": ["prometheus.query_prometheus_metrics"],
                        }
                    )

    if action == "prometheus.query_prometheus_range_metrics":
        series = payload.get("series")
        if isinstance(series, list) and series:
            hints.append(
                {
                    "hypothesis": "metric trend shows sustained anomaly during alert window",
                    "confidence": 0.58,
                    "evidence_refs": ["prometheus.query_prometheus_range_metrics:series"],
                    "tool_refs": ["prometheus.query_prometheus_range_metrics"],
                }
            )

    return hints


def _normalize_evidence(action: str, payload: Any, captured_at: str) -> Dict[str, Any]:
    parsed = payload if isinstance(payload, dict) else {"raw": payload}
    timeline_events: List[Dict[str, Any]] = []

    if action == "log.analyze_log_around_alert":
        timeline_events = _extract_log_timeline(parsed, captured_at)
        evidence_type = "log_summary"
    elif action == "prometheus.query_prometheus_metrics":
        timeline_events = _extract_metric_snapshot_timeline(parsed, captured_at)
        evidence_type = "metric_snapshot"
    elif action == "prometheus.query_prometheus_range_metrics":
        timeline_events = _extract_metric_range_timeline(parsed, captured_at)
        evidence_type = "metric_series"
    else:
        evidence_type = "tool_output"

    root_cause_hints = _extract_root_cause_hints(action, parsed)

    return {
        "evidence_type": evidence_type,
        "timeline_events": timeline_events,
        "root_cause_hints": root_cause_hints,
        "raw": parsed,
    }


def _timeline_fingerprint(item: Dict[str, Any]) -> str:
    seed = f"{item.get('time')}|{item.get('source')}|{item.get('event')}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _root_cause_fingerprint(item: Dict[str, Any]) -> str:
    seed = f"{item.get('hypothesis')}|{item.get('confidence')}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _merge_timeline_candidates(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = list(existing)
    seen = {_timeline_fingerprint(i) for i in merged if isinstance(i, dict)}

    for item in incoming:
        if not isinstance(item, dict):
            continue
        fp = _timeline_fingerprint(item)
        if fp in seen:
            continue
        merged.append(item)
        seen.add(fp)

    def _sort_key(x: Dict[str, Any]) -> str:
        t = x.get("time")
        return t if isinstance(t, str) else ""

    merged.sort(key=_sort_key)
    return merged


def _merge_root_cause_candidates(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged = list(existing)
    seen = {_root_cause_fingerprint(i) for i in merged if isinstance(i, dict)}

    for item in incoming:
        if not isinstance(item, dict):
            continue
        fp = _root_cause_fingerprint(item)
        if fp in seen:
            continue
        merged.append(item)
        seen.add(fp)

    merged.sort(key=lambda x: float(x.get("confidence") or 0), reverse=True)
    return merged


def _collect_evidence(
    messages: List[Any],
    existing: List[Dict[str, Any]],
    existing_timeline: List[Dict[str, Any]],
    existing_root_causes: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    evidence = list(existing)
    timeline_candidates = list(existing_timeline)
    root_cause_candidates = list(existing_root_causes)

    seen = {item.get("fingerprint") for item in evidence if isinstance(item, dict)}

    for msg in messages:
        if not isinstance(msg, ToolMessage):
            continue

        evidence_item = None
        if msg.name == "dispatch_tool":
            evidence_item = _extract_evidence_from_dispatch(msg)
        else:
            legacy_action = LEGACY_EVIDENCE_TOOLS.get(msg.name)
            if legacy_action:
                parsed = _safe_json_loads(msg.content)
                if parsed is None:
                    parsed = {"raw": str(msg.content)}
                evidence_item = (legacy_action, parsed)

        if not evidence_item:
            continue

        source_action, parsed_payload = evidence_item
        fp = _evidence_fingerprint(source_action, parsed_payload)
        if fp in seen:
            continue

        captured_at = _dt_to_iso_z(datetime.now(timezone.utc))
        normalized = _normalize_evidence(source_action, parsed_payload, captured_at)

        evidence.append(
            {
                "fingerprint": fp,
                "source": "tool",
                "tool": source_action,
                "captured_at": captured_at,
                "data": normalized,
            }
        )

        timeline_candidates = _merge_timeline_candidates(
            timeline_candidates,
            normalized.get("timeline_events") if isinstance(normalized, dict) else [],
        )
        root_cause_candidates = _merge_root_cause_candidates(
            root_cause_candidates,
            normalized.get("root_cause_hints") if isinstance(normalized, dict) else [],
        )

        seen.add(fp)

    return evidence, timeline_candidates, root_cause_candidates


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


def diagnoser_node(state: AgentState, config: Optional[RunnableConfig] = None):
    messages = state["messages"]
    alert_context = state.get("alert_context", {})
    alert_context_str = json.dumps(alert_context, ensure_ascii=False, indent=2)

    existing_evidence = state.get("evidence", [])
    existing_hypotheses = state.get("hypotheses", [])
    existing_timeline_candidates = state.get("timeline_candidates", [])
    existing_root_cause_candidates = state.get("root_cause_candidates", [])

    evidence, timeline_candidates, root_cause_candidates = _collect_evidence(
        messages,
        existing_evidence,
        existing_timeline_candidates,
        existing_root_cause_candidates,
    )
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

    response = chain.invoke({"history": messages, "alert_info": alert_context_str})

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

    hypotheses = _collect_hypotheses([response], hypotheses)

    return {
        "messages": [response],
        "evidence": evidence,
        "hypotheses": hypotheses,
        "timeline_candidates": timeline_candidates,
        "root_cause_candidates": root_cause_candidates,
    }
