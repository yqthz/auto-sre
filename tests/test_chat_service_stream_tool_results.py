import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from app.service.chat_service import ChatService


class FakeGraph:
    def __init__(self, first_events, existing_messages=None):
        self._first_events = first_events
        self._existing_messages = existing_messages or []

    def get_state(self, _config):
        return SimpleNamespace(values={"messages": self._existing_messages})

    async def astream(self, initial_input, config=None, stream_mode="values"):
        if initial_input is None:
            return
            yield
        for event in self._first_events:
            yield event


class ChatServiceStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_emits_all_tool_results_when_snapshot_contains_multiple_new_messages(self):
        service = ChatService()

        ai_tool_call = AIMessage(
            content="让我先查看可用工具。",
            tool_calls=[{"id": "call_1", "name": "dispatch_tool", "args": {"action": "a", "params": {}}}],
        )
        tool_msg_1 = ToolMessage(content="result-a", tool_call_id="call_1", name="dispatch_tool")
        tool_msg_2 = ToolMessage(content="result-b", tool_call_id="call_2", name="dispatch_tool")
        ai_final = AIMessage(content="最终结论")

        history_ai = AIMessage(content="历史回答")
        events = [
            {"messages": [ai_tool_call]},
            {"messages": [ai_tool_call, tool_msg_1, tool_msg_2, ai_final]},
        ]
        service.graph = FakeGraph(events, existing_messages=[history_ai])

        db = SimpleNamespace(commit=AsyncMock())
        session = SimpleNamespace(id=1, thread_id="t1", mode="auto", status="running")

        async def fake_save_message(*_args, **kwargs):
            return SimpleNamespace(id=100)

        service.save_message = AsyncMock(side_effect=fake_save_message)

        with patch("app.service.chat_service.trace_runtime.start_run", return_value="run-1"), \
             patch("app.service.chat_service.trace_runtime.end_run"), \
             patch("app.service.chat_service.tool_approval_profile", return_value={"requires_approval": False}), \
             patch.object(service, "_trace_tool_start"), \
             patch.object(service, "_audit_tool_request"), \
             patch.object(service, "_trace_tool_end"), \
             patch.object(service, "_audit_tool_result"):
            emitted = []
            async for item in service.stream_agent_response(
                db=db,
                session=session,
                user_message="当前运行的服务是什么",
                user_id=1,
                user_role="admin",
            ):
                emitted.append(item)

        tool_results = [e for e in emitted if e["event"] == "tool_call_result"]
        self.assertEqual(2, len(tool_results))
        self.assertEqual(["call_1", "call_2"], [e["data"]["tool_call_id"] for e in tool_results])


if __name__ == "__main__":
    asyncio.run(unittest.main())
