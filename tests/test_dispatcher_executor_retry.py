import unittest
from unittest.mock import patch

from app.agent.dispatcher.executor import dispatch_action
from app.agent.dispatcher.registry import ActionMeta


def _decision(meta: ActionMeta):
    return type("Decision", (), {"action_meta": meta, "status": "allowed", "reason": ""})


def _meta(max_retries: int = 1, retry_on_kinds=None, requires_approval: bool = False, risk_level: str = "low") -> ActionMeta:
    if retry_on_kinds is None:
        retry_on_kinds = ["timeout", "spawn_error", "cli_failed"]
    return ActionMeta(
        action="misc.fake",
        tool_name="fake",
        tool_group="misc",
        fn=lambda value: f"legacy:{value}",
        description="",
        doc="",
        roles=["viewer"],
        permission="info",
        requires_approval=requires_approval,
        risk_level=risk_level,
        required_params=["value"],
        param_types={"value": "string"},
        param_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        timeout_seconds=9,
        max_retries=max_retries,
        retry_backoff_seconds=0.01,
        retry_backoff_multiplier=2.0,
        retry_on_kinds=retry_on_kinds,
    )


class TestDispatcherExecutorRetry(unittest.TestCase):
    @patch("app.agent.dispatcher.executor.time.sleep")
    @patch("app.agent.dispatcher.executor.run_via_cli")
    @patch("app.agent.dispatcher.executor.evaluate_action")
    def test_retry_then_success(self, mock_eval, mock_cli, mock_sleep):
        mock_eval.return_value = _decision(_meta(max_retries=1))
        mock_cli.side_effect = [
            {"ok": False, "kind": "timeout", "error": "t1"},
            {"ok": True, "result": {"x": 1}, "kind": "success"},
        ]

        result = dispatch_action("misc.fake", {"value": "v"}, "viewer", "manual")

        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["execution_backend"], "cli")
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(len(result["retry_history"]), 1)
        mock_sleep.assert_called_once()

    @patch("app.agent.dispatcher.executor.time.sleep")
    @patch("app.agent.dispatcher.executor.run_via_cli", return_value={"ok": False, "kind": "invalid_output", "error": "bad"})
    @patch("app.agent.dispatcher.executor.evaluate_action")
    def test_non_retryable_error_fails_fast(self, mock_eval, _mock_cli, mock_sleep):
        mock_eval.return_value = _decision(_meta(max_retries=2, retry_on_kinds=["timeout"]))

        result = dispatch_action("misc.fake", {"value": "v"}, "viewer", "manual")

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(result["last_error_kind"], "invalid_output")
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
