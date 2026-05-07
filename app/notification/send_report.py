import json

from app.notification.email_notification import send_email
from app.storage import append_audit
from app.utils.format_utils import now_iso


def _render_report_markdown(title: str, report_content: str) -> str:
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
            evidence_ref = str(item.get("evidence_ref") or "unknown-evidence")
            lines.append(f"{idx}. Time: `{t}`")
            lines.append(f"   Source: `{src}`")
            lines.append(f"   Event: {event}")
            lines.append(f"   Evidence: {evidence_ref}")
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
            evidence_refs = item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []
            refs = [str(x) for x in evidence_refs if str(x).strip()]
            lines.append(f"{idx}. Hypothesis: {hypothesis}")
            lines.append(f"   Confidence: {confidence}")
            lines.append("   Evidence:")
            if refs:
                for ref in refs:
                    lines.append(f"   - {ref}")
            else:
                lines.append("   - N/A")
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
    if runbook_refs:
        for item in runbook_refs:
            lines.append(f"- {str(item)}")
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
