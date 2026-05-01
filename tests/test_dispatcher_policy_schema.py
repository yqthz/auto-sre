import unittest
from unittest.mock import patch

from app.agent.dispatcher.policy import evaluate_action
from app.agent.dispatcher.registry import ActionMeta


def _fake_meta() -> ActionMeta:
    return ActionMeta(
        action="log.analyze_log_around_alert",
        tool_name="analyze_log_around_alert",
        tool_group="log",
        fn=lambda **_: None,
        description="",
        roles=["viewer", "sre", "admin"],
        permission="info",
        requires_approval=False,
        risk_level="low",
        required_params=["log_file", "alert_time"],
        param_types={"log_file": "string", "alert_time": "string", "window_minutes": "int"},
        param_schema={
            "type": "object",
            "properties": {
                "log_file": {"type": "string", "minLength": 1},
                "alert_time": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T"},
                "window_minutes": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            "required": ["log_file", "alert_time"],
            "additionalProperties": False,
        },
    )


class TestDispatcherPolicySchema(unittest.TestCase):
    @patch("app.agent.dispatcher.policy.get_action_meta", return_value=_fake_meta())
    def test_missing_required_param_denied(self, _mock_meta):
        decision = evaluate_action(
            action="log.analyze_log_around_alert",
            params={"log_file": "/tmp/app.log"},
            user_role="viewer",
            mode="manual",
        )
        self.assertEqual(decision.status, "denied")
        self.assertIn("missing required params", decision.reason)

    @patch("app.agent.dispatcher.policy.get_action_meta", return_value=_fake_meta())
    def test_type_mismatch_denied(self, _mock_meta):
        decision = evaluate_action(
            action="log.analyze_log_around_alert",
            params={"log_file": "/tmp/app.log", "alert_time": "2026-04-26T00:00:00Z", "window_minutes": "5"},
            user_role="viewer",
            mode="manual",
        )
        self.assertEqual(decision.status, "denied")
        self.assertIn("type mismatch", decision.reason)

    @patch("app.agent.dispatcher.policy.get_action_meta", return_value=_fake_meta())
    def test_range_and_pattern_denied(self, _mock_meta):
        decision = evaluate_action(
            action="log.analyze_log_around_alert",
            params={"log_file": "/tmp/app.log", "alert_time": "invalid-time", "window_minutes": 80},
            user_role="viewer",
            mode="manual",
        )
        self.assertEqual(decision.status, "denied")
        self.assertIn("pattern", decision.reason)

    @patch("app.agent.dispatcher.policy.get_action_meta", return_value=_fake_meta())
    def test_unknown_param_denied(self, _mock_meta):
        decision = evaluate_action(
            action="log.analyze_log_around_alert",
            params={"log_file": "/tmp/app.log", "alert_time": "2026-04-26T00:00:00Z", "extra": "x"},
            user_role="viewer",
            mode="manual",
        )
        self.assertEqual(decision.status, "denied")
        self.assertIn("unknown params", decision.reason)

    @patch("app.agent.dispatcher.policy.get_action_meta", return_value=_fake_meta())
    def test_allowed_when_schema_valid(self, _mock_meta):
        decision = evaluate_action(
            action="log.analyze_log_around_alert",
            params={"log_file": "/tmp/app.log", "alert_time": "2026-04-26T00:00:00Z", "window_minutes": 5},
            user_role="viewer",
            mode="manual",
        )
        self.assertEqual(decision.status, "allowed")


if __name__ == "__main__":
    unittest.main()
