import time
import uuid
from typing import Any, Callable, Dict, Optional

from app.agent.trace_runtime import extract_usage_from_llm_response, trace_runtime


class LLMTrace:
    @staticmethod
    def on_llm_start(*, run_id: Optional[str], call_id: str, node_name: str, model: str, input_preview: str) -> None:
        if not run_id:
            return
        trace_runtime.append_event(
            run_id=run_id,
            event_type="llm_call_start",
            call_id=call_id,
            status="running",
            meta={
                "node_name": node_name,
                "model": model,
                "input_preview": input_preview,
            },
        )

    @staticmethod
    def on_llm_end(
        *,
        run_id: Optional[str],
        call_id: str,
        duration_ms: int,
        output_preview: str,
        usage: Optional[Dict[str, int]] = None,
    ) -> None:
        if not run_id:
            return
        meta: Dict[str, Any] = {"output_preview": output_preview}
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

    @staticmethod
    def on_llm_error(*, run_id: Optional[str], call_id: str, error: Exception) -> None:
        if not run_id:
            return
        trace_runtime.append_event(
            run_id=run_id,
            event_type="llm_call_error",
            call_id=call_id,
            status="failed",
            meta={
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        )

    @staticmethod
    def invoke(
        *,
        run_id: Optional[str],
        node_name: str,
        model: str,
        input_preview: str,
        invoke_fn: Callable[[], Any],
    ) -> Any:
        call_id = uuid.uuid4().hex
        started = time.time()
        LLMTrace.on_llm_start(
            run_id=run_id,
            call_id=call_id,
            node_name=node_name,
            model=model,
            input_preview=input_preview,
        )
        try:
            response = invoke_fn()
        except Exception as error:
            LLMTrace.on_llm_error(run_id=run_id, call_id=call_id, error=error)
            raise

        duration_ms = max(0, int((time.time() - started) * 1000))
        usage = extract_usage_from_llm_response(response)
        output_preview = response.content if isinstance(response.content, str) else str(response.content)
        LLMTrace.on_llm_end(
            run_id=run_id,
            call_id=call_id,
            duration_ms=duration_ms,
            output_preview=output_preview,
            usage=usage,
        )
        return response


class ToolTrace:
    @staticmethod
    def on_tool_start(*, run_id: Optional[str], tool_call: Dict[str, Any]) -> None:
        if not run_id:
            return
        call_id = str(tool_call.get("id") or uuid.uuid4().hex)
        trace_runtime.append_event(
            run_id=run_id,
            event_type="tool_call_start",
            call_id=call_id,
            status="running",
            meta={
                "tool_name": str(tool_call.get("name") or "unknown"),
                "args_preview": tool_call.get("args", {}),
            },
        )

    @staticmethod
    def on_tool_end(
        *,
        run_id: Optional[str],
        call_id: Optional[str],
        tool_name: str,
        output_preview: str,
        status: str = "success",
        duration_ms: Optional[int] = None,
    ) -> None:
        if not run_id:
            return
        resolved_call_id = str(call_id or uuid.uuid4().hex)
        trace_runtime.append_event(
            run_id=run_id,
            event_type="tool_call_end",
            call_id=resolved_call_id,
            status=status,
            duration_ms=duration_ms,
            meta={
                "tool_name": tool_name,
                "output_preview": output_preview,
                "status": status,
            },
        )

    @staticmethod
    def on_tool_error(*, run_id: Optional[str], call_id: Optional[str], tool_name: str, error: Exception) -> None:
        if not run_id:
            return
        resolved_call_id = str(call_id or uuid.uuid4().hex)
        trace_runtime.append_event(
            run_id=run_id,
            event_type="tool_call_error",
            call_id=resolved_call_id,
            status="failed",
            meta={
                "tool_name": tool_name,
                "error_type": type(error).__name__,
                "error_message": str(error),
            },
        )
