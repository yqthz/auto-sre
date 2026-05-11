import unittest
from unittest.mock import patch

from app.agent.dispatcher.discovery import cli_action_doc_payload, cli_list_payload
from app.agent.dispatcher.registry import ActionMeta


class TestDiscoverySchemaSource(unittest.TestCase):
    def test_cli_action_doc_returns_doc_string(self):
        fake = ActionMeta(
            action="misc.lookup_service_info",
            tool_name="lookup_service_info",
            tool_group="misc",
            fn=lambda **_: None,
            description="lookup service desc",
            doc="lookup service doc",
            roles=["viewer"],
            permission="info",
            requires_approval=False,
            risk_level="low",
            required_params=[],
            param_types={},
            param_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            timeout_seconds=10,
            max_retries=1,
            retry_backoff_seconds=0.5,
            retry_backoff_multiplier=2.0,
            retry_on_kinds=["timeout", "spawn_error", "cli_failed"],
        )

        with patch("app.agent.dispatcher.discovery.list_actions", return_value=[fake]):
            payload = cli_action_doc_payload(action="misc.lookup_service_info", user_role="viewer", mode="manual")
        self.assertEqual(payload, {"action": "misc.lookup_service_info", "doc": "lookup service doc"})

    def test_cli_list_returns_action_details(self):
        fake = ActionMeta(
            action="prometheus.query_prometheus_metrics",
            tool_name="query_prometheus_metrics",
            tool_group="prometheus",
            fn=lambda **_: None,
            description="prometheus list description",
            doc="prometheus doc string",
            roles=["viewer"],
            permission="info",
            requires_approval=False,
            risk_level="low",
            required_params=[],
            param_types={},
            param_schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            timeout_seconds=10,
            max_retries=1,
            retry_backoff_seconds=0.5,
            retry_backoff_multiplier=2.0,
            retry_on_kinds=["timeout", "spawn_error", "cli_failed"],
        )

        with patch("app.agent.dispatcher.discovery.list_actions", return_value=[fake]):
            payload = cli_list_payload(user_role="viewer", mode="manual")

        self.assertEqual(payload["tools"], [
            {
                "tool": "prometheus",
                "actions": [
                    {
                        "name": "prometheus.query_prometheus_metrics",
                        "description": "prometheus list description",
                        "risk_level": "low",
                        "requires_approval": False,
                    }
                ],
            }
        ])


if __name__ == "__main__":
    unittest.main()
