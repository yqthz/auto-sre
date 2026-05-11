from typing import Annotated, Any, Dict, List, Literal, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    user_role: Literal["admin", "sre", "viewer"]
    mode: Literal["auto", "manual"]
    messages: Annotated[List, add_messages]
    alert_context: Dict | None
    report: str | None
    # evidence: List[Dict[str, Any]]
    # hypotheses: List[Dict[str, Any]]
    # timeline_candidates: List[Dict[str, Any]]
    # root_cause_candidates: List[Dict[str, Any]]
    approval_requests: List[Dict[str, Any]]
    actions_executed: List[Dict[str, Any]]
    trace_run_id: str | None
