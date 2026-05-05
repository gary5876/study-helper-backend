"""Unit tests for app/routers/user.py — /user/* 엔드포인트.

require_current_user / get_user_store dependency를 override해서
실제 DB·JWT 없이 라우터 입출력만 검증.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.auth import require_current_user
from app.main import app
from app.services.user_store import get_user_store


_FAKE_USER = {"user_id": "11111111-1111-1111-1111-111111111111", "email": "u@e.com"}


@pytest.fixture
def fake_store():
    """매 테스트마다 새 mock store를 만들어 dependency override에 주입."""
    store = MagicMock()
    return store


@pytest.fixture
def client(fake_store):
    app.dependency_overrides[require_current_user] = lambda: _FAKE_USER
    app.dependency_overrides[get_user_store] = lambda: fake_store
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _now_iso():
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────
# Subjects
# ─────────────────────────────────────────

def test_list_subjects_returns_array(client, fake_store):
    fake_store.get_subjects = AsyncMock(return_value=[
        {"id": "s1", "name": "수학", "color": "#abcdef", "created_at": _now_iso()},
    ])
    res = client.get("/user/subjects")
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["name"] == "수학"


def test_create_subject_returns_201(client, fake_store):
    fake_store.create_subject = AsyncMock(return_value={
        "id": "s1", "name": "물리", "color": "#abcdef", "created_at": _now_iso(),
    })
    res = client.post("/user/subjects", json={"name": "물리", "color": "#abcdef"})
    assert res.status_code == 201
    assert res.json()["name"] == "물리"


def test_create_subject_invalid_color_returns_422(client, fake_store):
    res = client.post("/user/subjects", json={"name": "X", "color": "not-a-color"})
    assert res.status_code == 422  # Pydantic validation


def test_create_subject_empty_name_returns_422(client, fake_store):
    res = client.post("/user/subjects", json={"name": "", "color": "#abcdef"})
    assert res.status_code == 422


def test_delete_subject_success_returns_204(client, fake_store):
    fake_store.delete_subject = AsyncMock(return_value=True)
    res = client.delete("/user/subjects/abc-123")
    assert res.status_code == 204


def test_delete_subject_not_found_returns_404(client, fake_store):
    fake_store.delete_subject = AsyncMock(return_value=False)
    res = client.delete("/user/subjects/abc-123")
    assert res.status_code == 404


# ─────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────

def test_list_sessions_returns_array(client, fake_store):
    fake_store.get_sessions = AsyncMock(return_value=[
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "pdf_name": "x.pdf",
            "pdf_hash": "a" * 64,
            "subject_id": None,
            "page_count": 5,
            "word_count": 100,
            "status": "ready",
            "created_at": _now_iso(),
            "last_accessed": _now_iso(),
        },
    ])
    res = client.get("/user/sessions")
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_create_session_returns_201(client, fake_store):
    fake_store.create_session = AsyncMock(return_value={
        "id": "11111111-1111-1111-1111-111111111111",
        "pdf_name": "x.pdf",
        "pdf_hash": "a" * 64,
        "subject_id": None,
        "page_count": 5,
        "word_count": 100,
        "status": "pending",
        "created_at": _now_iso(),
        "last_accessed": None,
    })
    res = client.post("/user/sessions", json={
        "pdf_name": "x.pdf",
        "pdf_hash": "a" * 64,
        "page_count": 5,
        "word_count": 100,
    })
    assert res.status_code == 201
    assert res.json()["pdf_name"] == "x.pdf"


def test_create_session_invalid_status_returns_422(client, fake_store):
    res = client.post("/user/sessions", json={
        "pdf_name": "x.pdf",
        "status": "invalid-status-value",
    })
    assert res.status_code == 422


def test_delete_session_success_returns_204(client, fake_store):
    fake_store.delete_session = AsyncMock(return_value=True)
    res = client.delete("/user/sessions/11111111-1111-1111-1111-111111111111")
    assert res.status_code == 204


def test_delete_session_not_found_returns_404(client, fake_store):
    fake_store.delete_session = AsyncMock(return_value=False)
    res = client.delete("/user/sessions/11111111-1111-1111-1111-111111111111")
    assert res.status_code == 404


# ─────────────────────────────────────────
# Review Schedule
# ─────────────────────────────────────────

def test_get_review_schedule_returns_due(client, fake_store):
    fake_store.get_due_reviews = AsyncMock(return_value=[
        {
            "id": "rs1",
            "session_id": "11111111-1111-1111-1111-111111111111",
            "question_id": "q1",
            "question_type": "mcq",
            "interval_days": 1,
            "next_review_at": _now_iso(),
            "ease_factor": 2.5,
            "repetitions": 0,
            "status": "pending",
        },
    ])
    res = client.get("/user/review-schedule")
    assert res.status_code == 200
    assert len(res.json()) == 1


def test_upsert_review_returns_201(client, fake_store):
    payload = {
        "session_id": "11111111-1111-1111-1111-111111111111",
        "question_id": "q-abc",
        "question_type": "mcq",
        "interval_days": 1,
        "next_review_at": _now_iso().isoformat(),
        "ease_factor": 2.5,
        "repetitions": 0,
        "status": "pending",
    }
    fake_store.upsert_review = AsyncMock(return_value={"id": "rs2", **payload})
    res = client.post("/user/review-schedule", json=payload)
    assert res.status_code == 201
    assert res.json()["question_id"] == "q-abc"


def test_upsert_review_invalid_question_type_returns_422(client, fake_store):
    payload = {
        "session_id": "11111111-1111-1111-1111-111111111111",
        "question_id": "q-abc",
        "question_type": "bogus",
        "next_review_at": _now_iso().isoformat(),
    }
    res = client.post("/user/review-schedule", json=payload)
    assert res.status_code == 422


# ─────────────────────────────────────────
# Sync (로컬 → 클라우드)
# ─────────────────────────────────────────

def test_sync_empty_payload_returns_zeros(client, fake_store):
    fake_store.sync_subjects = AsyncMock(return_value=0)
    fake_store.sync_sessions = AsyncMock(return_value=0)
    fake_store.sync_reviews = AsyncMock(return_value=0)
    res = client.post("/user/sync", json={})
    assert res.status_code == 200
    assert res.json() == {
        "subjects_synced": 0,
        "sessions_synced": 0,
        "reviews_synced": 0,
    }


def test_sync_with_data_returns_counts(client, fake_store):
    fake_store.sync_subjects = AsyncMock(return_value=2)
    fake_store.sync_sessions = AsyncMock(return_value=1)
    fake_store.sync_reviews = AsyncMock(return_value=3)
    res = client.post("/user/sync", json={
        "subjects": [{"name": "A", "color": "#aabbcc"}, {"name": "B", "color": "#001122"}],
        "sessions": [{"pdf_name": "x.pdf"}],
        "review_schedule": [],
    })
    assert res.status_code == 200
    assert res.json() == {
        "subjects_synced": 2,
        "sessions_synced": 1,
        "reviews_synced": 3,
    }


# ─────────────────────────────────────────
# Auth 가드 — override 제거 후 401 확인
# ─────────────────────────────────────────

def test_user_endpoint_requires_auth(fake_store):
    """require_current_user를 override하지 않으면 401 (Authorization 헤더 없음)."""
    app.dependency_overrides[get_user_store] = lambda: fake_store
    try:
        with TestClient(app) as c:
            res = c.get("/user/subjects")
        assert res.status_code == 401
    finally:
        app.dependency_overrides.clear()
