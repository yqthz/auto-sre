from typing import Any, Dict, List

from app.agent.dispatcher.registry import ActionMeta, group_actions_by_tool, list_actions


def _can_use(action: ActionMeta, user_role: str, mode: str) -> bool:
    if user_role not in action.roles:
        return False
    if mode == "auto" and action.requires_approval:
        return False
    return True


def cli_list_payload(user_role: str, mode: str) -> Dict[str, Any]:
    actions = [a for a in list_actions() if _can_use(a, user_role=user_role, mode=mode)]
    grouped = group_actions_by_tool(actions)

    tools: List[Dict[str, Any]] = []
    for tool_group, items in grouped.items():
        if not items:
            continue

        risk_levels = {i.risk_level for i in items}
        if "high" in risk_levels:
            risk_level = "high"
        elif "medium" in risk_levels:
            risk_level = "medium"
        else:
            risk_level = "low"

        tools.append(
            {
                "tool": tool_group,
                "actions": [i.action for i in items],
                "summary": items[0].description or f"actions in {tool_group}",
                "risk_level": risk_level,
            }
        )

    return {"tools": sorted(tools, key=lambda x: x["tool"])}


def cli_tool_doc_payload(tool: str, user_role: str, mode: str) -> Dict[str, Any]:
    actions = [a for a in list_actions() if a.tool_group == tool and _can_use(a, user_role=user_role, mode=mode)]

    docs = []
    for item in actions:
        docs.append(
            {
                "action": item.action,
                "when_to_use": item.description or f"use {item.action} for diagnostics",
                "required_params": item.required_params,
                "param_schema": item.param_schema,
                "examples": [
                    {
                        "action": item.action,
                        "params": {k: "" for k in item.required_params},
                    }
                ],
                "risk_level": item.risk_level,
                "requires_approval": item.requires_approval,
            }
        )

    return {"tool": tool, "actions": docs}
