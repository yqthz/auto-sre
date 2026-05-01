import unittest
from unittest.mock import patch

from app.agent.dispatcher.discovery import cli_tool_doc_payload
from app.agent.dispatcher.registry import ActionMeta


class TestDiscoverySchemaSource(unittest.TestCase):
    def test_cli_tool_doc_uses_action_meta_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "service_name": {"type": "string", "minLength": 1},
            },
            "required": ["service_name"],
            "additionalProperties": False,
        }

        fake = ActionMeta(
            action="misc.lookup_service_info",
            tool_name="lookup_service_info",
            tool_group="misc",
            fn=lambda **_: None,
            description="lookup service",
            roles=["viewer"],
            permission="info",
            requires_approval=False,
            risk_level="low",
            required_params=["service_name"],
            param_types={"service_name": "string"},
            param_schema=schema,
        )

        with patch("app.agent.dispatcher.discovery.list_actions", return_value=[fake]):
            payload = cli_tool_doc_payload(tool="misc", user_role="viewer", mode="manual")

        self.assertEqual(payload["tool"], "misc")
        self.assertEqual(len(payload["actions"]), 1)
        self.assertEqual(payload["actions"][0]["param_schema"], schema)


if __name__ == "__main__":
    unittest.main()
