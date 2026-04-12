import json

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.agent.state import AgentState
from app.agent.prompts.report_system_prompt import REPORTER_SYSTEM_PROMPT
from app.core.logger import logger
from app.utils.llm_utils import get_llm

llm = get_llm()


def reporter_node(state: AgentState):
    messages = state["messages"]

    alert_context = state.get("alert_context", {})
    alert_context_str = json.dumps(alert_context, ensure_ascii=False, indent=2)

    prompt = ChatPromptTemplate.from_messages([
        ("system", REPORTER_SYSTEM_PROMPT),
        MessagesPlaceholder(variable_name="history"),
    ])

    chain = prompt | llm
    response = chain.invoke({"history": messages, "alert_info": alert_context_str})

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
        # Keep raw text for traceability, but persist a valid JSON wrapper.
        fallback = {
            "summary": "信息不足",
            "root_cause": "reporter 输出未通过 JSON 校验，请查看 raw_text。",
            "recommendations": ["检查告警上下文和工具输出后重试分析"],
            "raw_text": report_text,
            "error": str(e),
        }
        normalized_report = json.dumps(fallback, ensure_ascii=False)

    return {
        "report": normalized_report,
        "messages": [response]
    }