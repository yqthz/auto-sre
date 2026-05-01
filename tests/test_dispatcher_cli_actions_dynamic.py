import unittest
from unittest.mock import patch

from app.agent.dispatcher.cli_actions import has_cli_handler, run_cli_action
from app.agent.dispatcher.registry import ActionMeta


def _fake_handler(value: str):
    return f"ok:{value}"


def _fake_meta() -> ActionMeta:
    return ActionMeta(
        action="misc.fake",
        tool_name="fake",
        tool_group="misc",
        fn=_fake_handler,
        description="",
        roles=["viewer"],
        permission="info",
        requires_approval=False,
        risk_level="low",
        required_params=["value"],
        param_types={"value": "string"},
        param_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )


class TestCliActionsDynamic(unittest.TestCase):
    @patch("app.agent.dispatcher.cli_actions.get_action_meta", return_value=None)
    def test_has_cli_handler_false_when_unknown(self, _mock_meta):
        self.assertFalse(has_cli_handler("unknown.action"))

    @patch("app.agent.dispatcher.cli_actions.get_action_meta", return_value=_fake_meta())
    def test_run_cli_action_calls_meta_handler(self, _mock_meta):
        result = run_cli_action("misc.fake", {"value": "x"})
        self.assertEqual(result, "ok:x")


if __name__ == "__main__":
    unittest.main()
