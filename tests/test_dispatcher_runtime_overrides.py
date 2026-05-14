import unittest

from app.agent.dispatcher.registry import get_action_meta


class TestDispatcherRuntimeOverrides(unittest.TestCase):
    def test_io_bound_actions_have_extended_timeouts(self):
        expected = {
            "log.overview_log_issues": 45,
            "log.analyze_error_requests": 45,
            "log.analyze_log_around_alert": 45,
            "log.aggregate_log_by_uri": 45,
            "log.retrieve_log_context": 45,
            "actuator.check_actuator_health": 30,
            "network.curl_http_endpoint": 30,
        }

        for action, timeout in expected.items():
            meta = get_action_meta(action)
            self.assertIsNotNone(meta, f"action should exist: {action}")
            self.assertEqual(timeout, meta.timeout_seconds, f"timeout mismatch for {action}")


if __name__ == "__main__":
    unittest.main()
