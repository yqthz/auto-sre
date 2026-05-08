import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.user import get_users


def _make_user(user_id: int, email: str):
    now = datetime.utcnow()
    return SimpleNamespace(
        id=user_id,
        email=email,
        role="viewer",
        is_active=True,
        created_at=now,
        updated_at=now,
        last_login_at=None,
    )


class _FakeScalarResult:
    def __init__(self, items=None):
        self._items = items or []

    def all(self):
        return self._items


class _FakeExecuteResult:
    def __init__(self, *, count=None, items=None):
        self._count = count
        self._items = items

    def scalar(self):
        return self._count

    def scalars(self):
        return _FakeScalarResult(self._items)


class TestUserListFiltersSystemAccount(unittest.IsolatedAsyncioTestCase):
    async def test_system_account_is_filtered_from_list(self):
        db = SimpleNamespace()
        db.execute = AsyncMock(
            side_effect=[
                _FakeExecuteResult(count=2),
                _FakeExecuteResult(
                    items=[
                        _make_user(1, "system-autobot@auto-sre.local"),
                        _make_user(2, "alice@example.com"),
                    ]
                ),
            ]
        )
        current_user = SimpleNamespace(role="admin")

        response = await get_users(
            skip=0,
            limit=20,
            search=None,
            role=None,
            is_active=None,
            current_user=current_user,
            db=db,
        )

        self.assertEqual(response.total, 2)
        self.assertEqual(len(response.users), 1)
        self.assertEqual(response.users[0].email, "alice@example.com")


if __name__ == "__main__":
    unittest.main()

