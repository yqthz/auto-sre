import unittest

from fastapi.testclient import TestClient

from app.api import deps
from app.model.user import User
from main import app


class FakeResult:
    def __init__(self, scalar_value=None, rows=None):
        self._scalar_value = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar_value

    def all(self):
        return self._rows


class FakeAsyncSession:
    def __init__(self):
        self._results = [
            FakeResult(10),
            FakeResult(4),
            FakeResult(6),
            FakeResult(7),
            FakeResult(1),
            FakeResult(12.345),
            FakeResult(rows=[(10,), (20,), (30,)]),
            FakeResult(rows=[("critical", 3), ("warning", 7)]),
            FakeResult(rows=[("InstanceDown", 5)]),
        ]
        self._index = 0

    async def execute(self, query):
        result = self._results[self._index]
        self._index += 1
        return result

    def add(self, obj):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


class AlertStatsApiTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides.clear()
        app.dependency_overrides[deps.get_current_active_user] = self._override_current_user
        app.dependency_overrides[deps.get_db] = self._override_db

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

    async def _override_db(self):
        yield FakeAsyncSession()

    def test_get_alert_stats_does_not_return_error_signal(self):
        response = self.client.get("/api/v1/alerts/stats")

        assert response.status_code == 200
        body = response.json()
        assert "error_signal" not in body
        assert body["volume"]["total_alerts"] == 10
        assert body["analysis"]["success_rate"] == 0.875
        assert body["distribution"]["alert_name_top"][0]["name"] == "InstanceDown"


if __name__ == "__main__":
    unittest.main()
