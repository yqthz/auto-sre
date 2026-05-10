import json
from typing import Any, Dict, List

from app.notification.email_notification import send_email
from app.storage import append_audit
from app.utils.format_utils import now_iso


def _as_text_list(value: Any) -> List[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if not isinstance(value, list):
        return []

    result: List[str] = []
    for item in value:
        text = item if isinstance(item, str) else str(item)
        text = text.strip()
        if text:
            result.append(text)
    return result


def _evidence_items(item: Dict[str, Any]) -> List[str]:
    evidence = item.get("evidence")
    if evidence is None:
        evidence = item.get("evidence_refs")
    if evidence is None:
        evidence = item.get("evidence_ref")
    return _as_text_list(evidence)


def _append_evidence(lines: List[str], evidence: List[str], indent: int = 3) -> None:
    pad = " " * indent
    if not evidence:
        lines.append(f"{pad}- N/A")
        return

    for snippet in evidence:
        lines.append(f"{pad}-")
        lines.append(f"{pad}  ```text")
        for line in snippet.splitlines() or [""]:
            lines.append(f"{pad}  {line}")
        lines.append(f"{pad}  ```")


def _runbook_name(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        for key in ("document_name", "doc_name", "title", "name", "filename"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(item).strip()


def _render_report_markdown(title: str, report_content: Any) -> str:
    if isinstance(report_content, (dict, list)):
        payload = report_content
    else:
        try:
            payload = json.loads(report_content)
        except Exception:
            return f"# {title}\n\n```json\n{report_content}\n```\n"

    if not isinstance(payload, dict):
        return f"# {title}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```\n"

    summary = str(payload.get("summary") or "")
    severity = str(payload.get("severity") or "")
    impact_scope = str(payload.get("impact_scope") or "")
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
    root_causes = payload.get("root_causes") if isinstance(payload.get("root_causes"), list) else []
    recommendations = payload.get("recommendations") if isinstance(payload.get("recommendations"), list) else []
    runbook_refs = payload.get("runbook_refs") if isinstance(payload.get("runbook_refs"), list) else []
    risk_notes = str(payload.get("risk_notes") or "")

    lines = [
        f"# {title}",
        "",
        "## Summary",
        summary or "N/A",
        "",
        "## Severity",
        severity or "N/A",
        "",
        "## Impact Scope",
        impact_scope or "N/A",
        "",
        "## Timeline",
    ]

    if timeline:
        idx = 1
        for item in timeline:
            if not isinstance(item, dict):
                continue
            t = str(item.get("time") or "unknown-time")
            src = str(item.get("source") or "unknown-source")
            event = str(item.get("event") or "unknown-event")
            lines.append(f"{idx}. Time: `{t}`")
            lines.append(f"   Source: `{src}`")
            lines.append(f"   Event: {event}")
            lines.append("   Evidence:")
            _append_evidence(lines, _evidence_items(item))
            lines.append("")
            idx += 1
    else:
        lines.append("- N/A")

    lines.extend(["", "## Root Causes"])
    if root_causes:
        idx = 1
        for item in root_causes:
            if not isinstance(item, dict):
                continue
            hypothesis = str(item.get("hypothesis") or "unknown")
            confidence = item.get("confidence")
            reasoning = str(item.get("reasoning") or "").strip()
            lines.append(f"{idx}. Hypothesis: {hypothesis}")
            lines.append(f"   Confidence: {confidence}")
            if reasoning:
                lines.append(f"   Reasoning: {reasoning}")
            lines.append("   Evidence:")
            _append_evidence(lines, _evidence_items(item))
            lines.append("")
            idx += 1
    else:
        lines.append("- N/A")

    lines.extend(["", "## Recommendations"])
    if recommendations:
        for item in recommendations:
            lines.append(f"- {str(item)}")
    else:
        lines.append("- N/A")

    lines.extend(["", "## Runbook Refs"])
    names = [_runbook_name(item) for item in runbook_refs]
    names = [name for name in names if name]
    if names:
        for name in names:
            lines.append(f"- {name}")
    else:
        lines.append("- N/A")

    lines.extend(["", "## Risk Notes", risk_notes or "N/A", ""])
    return "\n".join(lines)


def broadcase_report(report_content: str, alert_name: str = "System Alert"):
    """Broadcast notification: send to configured channels."""
    title = f"Incident Diagnostic Report: {alert_name}"
    markdown = _render_report_markdown(title, report_content)

    # send_email(title, report_content)
    with open("report.md", "w", encoding="utf-8") as f:
        f.write(markdown)
    append_audit({
        "timestamp": now_iso(),
        "event": "notification_send",
        "user_id": "system",
        "user_role": "system",
        "status": "success",
        "channel": "file",
        "title": title,
        "path": "report.md",
    })
