import json
import unittest
from datetime import datetime, timedelta, timezone

from app.agent.dispatcher.executor import dispatch_action
from app.agent.dispatcher.registry import ACTION_RUNTIME_OVERRIDES
from app.agent.runtime_profile import get_runtime_profile


def _dispatch(action: str, params: dict, user_role: str = "sre") -> dict:
    return dispatch_action(
        action=action,
        params=params,
        user_role=user_role,
        mode="manual",
    )


def _loads_tool_result(result: dict):
    payload = result.get("result")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload


class TestToolsLiveInvocation(unittest.TestCase):
    """Live smoke tests for tool invocation against deployed external services."""

    @classmethod
    def setUpClass(cls):
        cls.profile = get_runtime_profile()
        cls.now = datetime.now(timezone.utc)
        cls.alert_time = cls.now.isoformat()
        cls.start_time = (cls.now - timedelta(minutes=5)).isoformat()
        cls.end_time = cls.now.isoformat()
        cls.prom_instance = f"{cls.profile.app.container_name}:28089"
        ACTION_RUNTIME_OVERRIDES.update(
            {
                "rag.search_knowledge_base": {
                    "timeout_seconds": 60,
                    "max_retries": 0,
                },
                "rag.get_knowledge_document": {
                    "timeout_seconds": 30,
                    "max_retries": 0,
                },
                "rag.get_knowledge_document_context": {
                    "timeout_seconds": 30,
                    "max_retries": 0,
                },
            }
        )

    def assert_dispatched(self, action: str, params: dict, user_role: str = "sre"):
        result = _dispatch(action, params, user_role=user_role)
        self.assertEqual(result["status"], "executed", msg=result)
        self.assertEqual(result["execution_backend"], "cli", msg=result)
        return result, _loads_tool_result(result)

    def assert_tool_ok(self, action: str, params: dict, user_role: str = "sre"):
        result, payload = self.assert_dispatched(action, params, user_role=user_role)
        if isinstance(payload, dict) and "ok" in payload:
            self.assertTrue(payload["ok"], msg={"action": action, "payload": payload, "result": result})
        return payload

    def test_profile_tool(self):
        payload = self.assert_tool_ok("profile.lookup_runtime_profile", {}, user_role="viewer")
        self.assertIn("profile", payload)

    def test_actuator_tools(self):
        self.assert_tool_ok("actuator.check_actuator_health", {}, user_role="viewer")
        metrics = self.assert_tool_ok("actuator.list_actuator_metrics", {}, user_role="viewer")
        metric_name = "jvm.memory.used"
        if isinstance(metrics, dict) and metrics.get("names"):
            metric_name = metrics["names"][0]
        self.assert_tool_ok(
            "actuator.get_actuator_metric",
            {"metric_name": metric_name},
            user_role="viewer",
        )
        self.assert_tool_ok("actuator.get_actuator_threaddump", {}, user_role="sre")

    def test_docker_tools(self):
        ps_payload = self.assert_tool_ok("docker.docker_compose_ps", {}, user_role="viewer")
        services = {
            item.get("Service")
            for item in ps_payload.get("services") or []
            if isinstance(item, dict)
        }
        self.assertIn(self.profile.app.service_name, services)
        self.assertIn(self.profile.mysql.service_name, services)
        self.assert_tool_ok(
            "docker.docker_inspect_container",
            {"container_name": self.profile.app.container_name},
            user_role="viewer",
        )
        self.assert_tool_ok(
            "docker.docker_inspect_container",
            {"container_name": self.profile.mysql.container_name},
            user_role="viewer",
        )
        self.assert_tool_ok(
            "docker.docker_compose_logs",
            {"service": self.profile.app.service_name, "tail": 50},
            user_role="viewer",
        )
        self.assert_tool_ok("docker.docker_service_status_summary", {}, user_role="viewer")

    def test_network_tools(self):
        self.assert_tool_ok(
            "network.check_network_connectivity",
            {"target_host": "localhost", "port": self.profile.mysql.host_port, "timeout": 2.0},
            user_role="viewer",
        )
        self.assert_tool_ok(
            "network.check_db_tcp_connectivity",
            {
                "db_host": self.profile.mysql.host,
                "db_port": self.profile.mysql.host_port,
                "timeout": 2.0,
            },
            user_role="viewer",
        )
        self.assert_tool_ok(
            "network.curl_http_endpoint",
            {"url": self.profile.app.host_base_url + self.profile.app.health_endpoint, "method": "GET"},
            user_role="viewer",
        )

    def test_prometheus_tools(self):
        self.assert_tool_ok("prometheus.query_prometheus_targets", {}, user_role="viewer")
        self.assert_dispatched("prometheus.query_prometheus_targets_health", {}, user_role="viewer")
        self.assert_tool_ok("prometheus.query_prometheus_alerts", {}, user_role="viewer")
        self.assert_dispatched(
            "prometheus.query_prometheus_metrics",
            {"alert_name": "InstanceDown", "instance": self.prom_instance},
            user_role="viewer",
        )
        self.assert_dispatched(
            "prometheus.query_prometheus_range_metrics",
            {
                "alert_name": "InstanceDown",
                "instance": self.prom_instance,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "step_seconds": 30,
            },
            user_role="viewer",
        )
        self.assert_dispatched(
            "prometheus.query_prometheus_by_promql",
            {"promql": "up", "mode": "instant"},
            user_role="sre",
        )

    def test_log_tools(self):
        self.assert_dispatched(
            "log.analyze_log_around_alert",
            {"alert_time": self.alert_time, "window_minutes": 5},
            user_role="viewer",
        )
        self.assert_dispatched(
            "log.retrieve_log_context",
            {
                "pattern": "ERROR",
                "alert_time": self.alert_time,
                "window_minutes": 60,
                "context_lines": 1,
                "max_matches": 1,
            },
            user_role="viewer",
        )
        self.assert_dispatched(
            "log.retrieve_log_context_raw",
            {
                "pattern": "ERROR",
                "alert_time": self.alert_time,
                "window_minutes": 60,
                "context_lines": 1,
                "max_matches": 1,
            },
            user_role="viewer",
        )

    def test_rag_tools(self):
        kb_payload = self.assert_tool_ok("rag.list_knowledge_bases", {}, user_role="viewer")
        search_payload = self.assert_tool_ok(
            "rag.search_knowledge_base",
            {"query": "error", "top_k": 1},
            user_role="viewer",
        )

        document_id = None
        if isinstance(search_payload, dict) and search_payload.get("results"):
            document_id = search_payload["results"][0].get("document_id")
        if document_id is None and isinstance(kb_payload, dict):
            document_id = 1

        self.assert_dispatched(
            "rag.get_knowledge_document",
            {"document_id": int(document_id or 1)},
            user_role="viewer",
        )
        self.assert_dispatched(
            "rag.get_knowledge_document_context",
            {"document_id": int(document_id or 1), "query": "error"},
            user_role="viewer",
        )


if __name__ == "__main__":
    unittest.main()
