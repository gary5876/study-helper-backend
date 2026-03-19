"""Integration tests for /generate, /status, /result endpoints."""
from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.session_store import init_store, get_store, SessionRecord


@pytest.fixture(autouse=True)
async def setup_store():
    await init_store(redis_url=None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _fake_parsed_doc():
    from app.services.pdf_parser import ParsedDocument, ParsedSection
    return ParsedDocument(
        sections=[ParsedSection(title="Intro", content="AI is the future of computing.", page_range=(1, 1))],
        full_text="AI is the future of computing.",
        page_count=1,
        word_count=30,
        truncated=False,
    )


def _upload(client) -> str:
    with patch("app.routers.upload.parse_pdf", return_value=_fake_parsed_doc()):
        res = client.post(
            "/upload",
            data={"api_key": "sk-ant-test-key-valid-12345"},
            files={"file": ("test.pdf", io.BytesIO(b"fake"), "application/pdf")},
        )
    assert res.status_code == 200
    return res.json()["session_id"]


# ─────────────────────────────────────────
# /generate
# ─────────────────────────────────────────

def test_generate_returns_processing(client):
    session_id = _upload(client)
    res = client.post("/generate", json={"session_id": session_id, "api_key": "sk-ant-test-key-valid-12345"})
    assert res.status_code == 200
    assert res.json()["status"] == "processing"


def test_generate_missing_session(client):
    res = client.post("/generate", json={"session_id": "fake-id", "api_key": "sk-ant-test-key-12345"})
    assert res.status_code == 404


def test_generate_already_processing_returns_processing(client):
    """Calling /generate twice should return 'processing' on the second call too."""
    session_id = _upload(client)
    client.post("/generate", json={"session_id": session_id, "api_key": "sk-ant-test-key-valid-12345"})
    res = client.post("/generate", json={"session_id": session_id, "api_key": "sk-ant-test-key-valid-12345"})
    assert res.status_code == 200
    assert res.json()["status"] in ("processing", "complete")


# ─────────────────────────────────────────
# /result
# ─────────────────────────────────────────

def test_result_not_started_returns_400(client):
    session_id = _upload(client)
    res = client.get(f"/result/{session_id}")
    assert res.status_code == 400


async def _inject_complete_session(session_id: str):
    """Helper: manually set a session to 'complete' with fake result JSON."""
    from app.models.schemas import StudyContent, StudyNotes, ContentMetadata
    content = {
        "session_id": session_id,
        "notes": {
            "key_concepts": [{"id": "c1", "term": "AI", "definition": "Artificial Intelligence", "importance": "high"}],
            "sections": [{"title": "Intro", "summary": "Overview", "bullets": ["AI is important"]}],
            "glossary": [{"term": "AI", "brief_def": "Artificial Intelligence"}],
        },
        "mcq_questions": [],
        "fill_questions": [],
        "metadata": {
            "page_count": 1, "word_count": 30, "generated_at": "2024-01-01T00:00:00+00:00",
            "model_used": "claude-sonnet-4-6", "section_count": 1,
        },
    }
    store = get_store()
    await store.update_status(
        session_id, "complete", progress_pct=100, result_json=json.dumps(content)
    )


@pytest.mark.asyncio
async def test_result_complete_returns_200(client):
    session_id = _upload(client)
    await _inject_complete_session(session_id)
    res = client.get(f"/result/{session_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == session_id
    assert "notes" in data


@pytest.mark.asyncio
async def test_result_failed_returns_500(client):
    session_id = _upload(client)
    store = get_store()
    await store.update_status(session_id, "failed", error_message="API error")
    res = client.get(f"/result/{session_id}")
    assert res.status_code == 500


# ─────────────────────────────────────────
# /status
# ─────────────────────────────────────────

def test_status_uploaded(client):
    session_id = _upload(client)
    res = client.get(f"/status/{session_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "uploaded"
    assert data["progress_pct"] == 0


@pytest.mark.asyncio
async def test_status_reflects_processing(client):
    session_id = _upload(client)
    store = get_store()
    await store.update_status(session_id, "processing", progress_pct=45)
    res = client.get(f"/status/{session_id}")
    assert res.json()["progress_pct"] == 45
    assert res.json()["status"] == "processing"
