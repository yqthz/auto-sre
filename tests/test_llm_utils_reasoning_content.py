import unittest

from langchain_core.messages import AIMessage, HumanMessage

from app.utils.llm_utils import _preserve_reasoning_content_in_payload


class TestLLMUtilsReasoningContent(unittest.TestCase):
    def test_preserve_reasoning_content_for_assistant_message(self):
        messages = [
            HumanMessage(content="hello"),
            AIMessage(content="tool call", additional_kwargs={"reasoning_content": "internal reasoning"}),
        ]
        payload = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "tool call", "tool_calls": []},
            ]
        }

        result = _preserve_reasoning_content_in_payload(messages, payload)

        self.assertEqual(result["messages"][1]["reasoning_content"], "internal reasoning")

    def test_keep_existing_reasoning_content(self):
        messages = [
            AIMessage(content="tool call", additional_kwargs={"reasoning_content": "internal reasoning"}),
        ]
        payload = {
            "messages": [
                {"role": "assistant", "content": "tool call", "reasoning_content": "already present"},
            ]
        }

        result = _preserve_reasoning_content_in_payload(messages, payload)

        self.assertEqual(result["messages"][0]["reasoning_content"], "already present")


if __name__ == "__main__":
    unittest.main()
