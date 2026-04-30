"""Unit tests for /generate, /status, /result, /session endpoints (app/routers/generate.py)."""
from __future__ import annotations

import io
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.services.session_store import init_store, get_store, SessionRecord
from app.services.pdf_parser import ParsedDocument, ParsedSection


@pytest.fixture(autouse=True)
async def setup_store():
    await init_store(redis_url=None)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _fake_doc() -> ParsedDocument:
    return ParsedDocument(
        sections=[ParsedSection(title="Intro", content="AI powers the future.", page_range=(1, 1))],
        full_text="AI powers the future.",
        page_count=2,
        word_count=50,
        truncated=False,
    )


def _upload(client) -> str:
    with patch("app.routers.upload.parse_pdf", return_value=_fake_doc()):
        res = client.post(
            "/upload",
            data={"api_key": "sk-ant-valid-key-12345", "plan": "paid"},
            files={"file": ("doc.pdf", io.BytesIO(b"fake"), "application/pdf")},
        )
    assert res.status_code == 200
    return res.json()["session_id"]


# ─────────────────────────────────────────
# POST /generate
# ─────────────────────────────────────────

def test_generate_missing_session_returns_404(client):
    res = client.post(
        "/generate",
        json={"session_id": "00000000-0000-0000-0000-000000000000", "api_key": "sk-ant-valid-key-12345"},
    )
    assert res.status_code == 404


def test_generate_missing_api_key_returns_400(client):
    session_id = _upload(client)
    res = client.post("/generate", json={"session_id": session_id, "api_key": ""})
    assert res.status_code == 400


def test_generate_starts_processing(client):
    session_id = _upload(client)
    with patch("app.routers.generate._run_generation", new_callable=AsyncMock):
        res = client.post("/generate", json={"session_id": session_id, "api_key": "sk-ant-valid-key-12345"})
    assert res.status_code == 200
    assert res.json()["status"] == "processing"


def test_generate_already_complete_returns_complete(client):
    session_id = _upload(client)
    # Manually mark as complete
    import asyncio
    async def _set_complete():
        store = get_store()
        await store.update_status(session_id, "complete", progress_pct=100, result_json='{"done": true}')
    asyncio.get_event_loop().run_until_complete(_set_complete())

    res = client.post("/generate", json={"session_id": session_id, "api_key": "sk-ant-valid-key-12345"})
    assert res.status_code == 200
    assert res.json()["status"] == "complete"


def test_generate_already_processing_returns_processing(client):
    session_id = _upload(client)
    import asyncio
    async def _set_processing():
        store = get_store()
        await store.update_status(session_id, "processing", progress_pct=30)
    asyncio.get_event_loop().run_until_complete(_set_processing())

    res = client.post("/generate", json={"session_id": session_id, "api_key": "sk-ant-valid-key-12345"})
    assert res.status_code == 200
    assert res.json()["status"] == "processing"


# ─────────────────────────────────────────
# GET /status/{session_id}
# ─────────────────────────────────────────

def test_status_not_found_returns_404(client):
    res = client.get("/status/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


def test_status_uploaded_returns_correct_state(client):
    session_id = _upload(client)
    res = client.get(f"/status/{session_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "uploaded"
    assert data["progress_pct"] == 0
    assert data["session_id"] == session_id


def test_status_reflects_progress(client):
    session_id = _upload(client)
    import asyncio
    async def _set():
        store = get_store()
        await store.update_status(session_id, "processing", progress_pct=65)
    asyncio.get_event_loop().run_until_complete(_set())

    res = client.get(f"/status/{session_id}")
    assert res.json()["progress_pct"] == 65
    assert res.json()["status"] == "processing"


# ─────────────────────────────────────────
# GET /result/{session_id}
# ─────────────────────────────────────────

def test_result_not_found_returns_404(client):
    res = client.get("/result/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404


def test_result_not_started_returns_400(client):
    session_id = _upload(client)
    res = client.get(f"/result/{session_id}")
    assert res.status_code == 400


def test_result_processing_returns_202(client):
    session_id = _upload(client)
    import asyncio
    async def _set():
        store = get_store()
        await store.update_status(session_id, "processing", progress_pct=50)
    asyncio.get_event_loop().run_until_complete(_set())

    res = client.get(f"/result/{session_id}")
    assert res.status_code == 202


def test_result_failed_returns_500(client):
    session_id = _upload(client)
    import asyncio
    async def _set():
        store = get_store()
        await store.update_status(session_id, "failed", error_message="LLM error")
    asyncio.get_event_loop().run_until_complete(_set())

    res = client.get(f"/result/{session_id}")
    assert res.status_code == 500


def test_result_complete_returns_200(client):
    session_id = _upload(client)
    result_payload = {
        "session_id": session_id,
        "notes": {
            "key_concepts": [{"id": "c1", "term": "AI", "definition": "Artificial Intelligence", "importance": "high"}],
            "sections": [{"title": "Intro", "summary": "Overview of AI", "bullets": ["AI is key"]}],
            "glossary": [{"term": "AI", "brief_def": "Artificial Intelligence"}],
        },
        "mcq_questions": [],
        "fill_questions": [],
        "metadata": {
            "page_count": 2,
            "word_count": 50,
            "generated_at": "2026-04-07T00:00:00+00:00",
            "model_used": "claude-sonnet-4-6",
            "section_count": 1,
        },
    }
    import asyncio
    async def _set():
        store = get_store()
        await store.update_status(session_id, "complete", progress_pct=100, result_json=json.dumps(result_payload))
    asyncio.get_event_loop().run_until_complete(_set())

    res = client.get(f"/result/{session_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["session_id"] == session_id
    assert "notes" in data


# ─────────────────────────────────────────
# DELETE /session/{session_id}
# ─────────────────────────────────────────

def test_delete_existing_session(client):
    session_id = _upload(client)
    res = client.delete(f"/session/{session_id}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True

    # Confirm gone
    assert client.get(f"/status/{session_id}").status_code == 404


def test_delete_nonexistent_returns_200_with_false(client):
    res = client.delete("/session/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 200
    assert res.json()["deleted"] is False
