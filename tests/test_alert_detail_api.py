from datetime import datetime
from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from app.api import deps
from app.model.user import User
from main import app


class FakeScalarResult:
    def __init__(self, event):
        self._event = event

    def scalars(self):
        return self

    def first(self):
        return self._event


class FakeAsyncSession:
    def __init__(self, event):
        self._event = event

    async def execute(self, query):
        return FakeScalarResult(self._event)

    def add(self, obj):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


class AlertDetailApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides.clear()
        app.dependency_overrides[deps.get_current_active_user] = self._override_current_user

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()

    async def _override_current_user(self):
        return User(
            id=1,
            email="viewer@example.com",
            hashed_password="x",
            role="viewer",
            is_active=True,
        )

    def _override_db(self, event):
        async def _get_db():
            yield FakeAsyncSession(event)

        return _get_db

    def test_get_alert_detail_returns_report_markdown(self):
        event = SimpleNamespace(
            id=101,
            alert_name="InstanceDown",
            severity="critical",
            status="firing",
            instance="10.0.0.1:8080",
            labels={"alertname": "InstanceDown"},
            annotations={"summary": "service unavailable"},
            starts_at=datetime(2026, 5, 10, 9, 44, 45),
            ends_at=None,
            analysis_status="done",
            session_id=88,
            metrics_snapshot={"queried_at": "2026-05-10T09:45:00Z"},
            log_summary={"entries": [{"count": 2, "message": "error"}]},
            analysis_report={
                "summary": "service stopped after a container exit",
                "severity": "critical",
                "impact_scope": "backend service",
                "timeline": [
                    {
                        "time": "2026-05-10T09:45:00Z",
                        "source": "log",
                        "event": "container exited",
                        "evidence": ["exit code 143"],
                    }
                ],
                "root_causes": [
                    {
                        "hypothesis": "container received SIGTERM",
                        "confidence": 0.9,
                        "evidence": ["exit code 143"],
                        "reasoning": "the process shut down cleanly",
                    }
                ],
                "recommendations": ["restart the service"],
                "runbook_refs": ["service-recovery"],
                "risk_notes": "restart may interrupt traffic",
            },
        )
        app.dependency_overrides[deps.get_db] = self._override_db(event)

        response = self.client.get("/api/v1/alerts/101")

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == 101
        assert body["report_markdown"].startswith("# Incident Diagnostic Report: InstanceDown")
        assert "## Summary" in body["report_markdown"]
        assert "restart the service" in body["report_markdown"]
        assert "analysis_report" not in body


if __name__ == "__main__":
    unittest.main()
