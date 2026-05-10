from datetime import datetime
import unittest

from fastapi.testclient import TestClient

from app.api import deps
from app.model.knowledge_base import Document
from app.model.user import User
from main import app


class FakeScalarResult:
    def __init__(self, document):
        self._document = document

    def scalars(self):
        return self

    def first(self):
        return self._document


class FakeAsyncSession:
    def __init__(self, document):
        self._document = document

    async def execute(self, query):
        return FakeScalarResult(self._document)


class DocumentPreviewApiTest(unittest.TestCase):
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

    def _override_db(self, document):
        async def _get_db():
            yield FakeAsyncSession(document)

        return _get_db

    def test_get_document_preview_returns_text(self):
        document = Document(
            id=11,
            kb_id=2,
            filename="sample.md",
            file_hash="abc123",
            file_size=123,
            file_type="md",
            content_text="line one\nline two",
            status="completed",
            error_message=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            chunk_count=2,
        )
        app.dependency_overrides[deps.get_db] = self._override_db(document)

        response = self.client.get("/api/v1/rag/documents/11/preview")

        assert response.status_code == 200
        assert response.json() == {"preview_text": "line one\nline two"}

    def test_get_document_preview_rejects_unfinished_document(self):
        document = Document(
            id=12,
            kb_id=2,
            filename="sample.md",
            file_hash="abc124",
            file_size=123,
            file_type="md",
            content_text="draft content",
            status="processing",
            error_message=None,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            chunk_count=0,
        )
        app.dependency_overrides[deps.get_db] = self._override_db(document)

        response = self.client.get("/api/v1/rag/documents/12/preview")

        assert response.status_code == 409
        assert response.json()["detail"] == "Document is not ready for preview"

    def test_get_document_preview_returns_404_when_missing(self):
        app.dependency_overrides[deps.get_db] = self._override_db(None)

        response = self.client.get("/api/v1/rag/documents/999/preview")

        assert response.status_code == 404
        assert response.json()["detail"] == "Document not found"


if __name__ == "__main__":
    unittest.main()
