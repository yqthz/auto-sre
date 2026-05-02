import os
import unittest
from unittest.mock import patch

from app.agent.dispatcher.executor import dispatch_action
from app.agent.dispatcher.registry import ActionMeta


def _fake_meta() -> ActionMeta:
    return ActionMeta(
        action="misc.fake",
        tool_name="fake",
        tool_group="misc",
        fn=lambda value: f"legacy:{value}",
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
        timeout_seconds=10,
        max_retries=0,
        retry_backoff_seconds=0.0,
        retry_backoff_multiplier=2.0,
        retry_on_kinds=["timeout", "spawn_error", "cli_failed"],
    )


class TestExecutorFallbackControls(unittest.TestCase):
    def setUp(self):
        self._old_enable = os.environ.get("DISPATCHER_ENABLE_LEGACY_FALLBACK")
        self._old_allowlist = os.environ.get("DISPATCHER_FALLBACK_ACTION_ALLOWLIST")

    def tearDown(self):
        if self._old_enable is None:
            os.environ.pop("DISPATCHER_ENABLE_LEGACY_FALLBACK", None)
        else:
            os.environ["DISPATCHER_ENABLE_LEGACY_FALLBACK"] = self._old_enable

        if self._old_allowlist is None:
            os.environ.pop("DISPATCHER_FALLBACK_ACTION_ALLOWLIST", None)
        else:
            os.environ["DISPATCHER_FALLBACK_ACTION_ALLOWLIST"] = self._old_allowlist

    @patch("app.agent.dispatcher.executor.run_via_cli", return_value={"ok": False, "kind": "cli_failed", "error": "boom"})
    @patch("app.agent.dispatcher.executor.evaluate_action")
    def test_fallback_disabled_by_default(self, mock_eval, _mock_cli):
        mock_eval.return_value = type("Decision", (), {"action_meta": _fake_meta(), "status": "allowed", "reason": ""})
        os.environ.pop("DISPATCHER_ENABLE_LEGACY_FALLBACK", None)

        result = dispatch_action("misc.fake", {"value": "v"}, "viewer", "manual")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["execution_backend"], "cli_failed")

    @patch("app.agent.dispatcher.executor.run_via_cli", return_value={"ok": False, "kind": "cli_failed", "error": "boom"})
    @patch("app.agent.dispatcher.executor.evaluate_action")
    def test_fallback_enabled_for_allowlisted_action(self, mock_eval, _mock_cli):
        mock_eval.return_value = type("Decision", (), {"action_meta": _fake_meta(), "status": "allowed", "reason": ""})
        os.environ["DISPATCHER_ENABLE_LEGACY_FALLBACK"] = "1"
        os.environ["DISPATCHER_FALLBACK_ACTION_ALLOWLIST"] = "misc.fake"

        result = dispatch_action("misc.fake", {"value": "v"}, "viewer", "manual")
        self.assertEqual(result["status"], "executed")
        self.assertEqual(result["execution_backend"], "legacy_fallback")


if __name__ == "__main__":
    unittest.main()
