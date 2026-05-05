"""Unit tests for app/services/user_store.py.

asyncpg.Pool은 AsyncMock으로 대체. 실제 DB 없이 SQL 발급 + 결과 처리를 검증.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.services.user_store import UserStore, get_user_store, init_user_store


# ─────────────────────────────────────────
# asyncpg pool/conn mock helpers
# ─────────────────────────────────────────

class _AcquireCM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return None


def _make_pool(conn):
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCM(conn))
    return pool


def _make_conn(**method_returns):
    conn = AsyncMock()
    for name, value in method_returns.items():
        setattr(conn, name, AsyncMock(return_value=value))
    return conn


# ─────────────────────────────────────────
# init / pool
# ─────────────────────────────────────────

async def test_init_user_store_handles_connection_failure(monkeypatch):
    async def _fail(*a, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr("app.services.user_store.asyncpg.create_pool", _fail)
    await init_user_store("postgresql://bad")
    # 실패 시 _pool은 None으로 유지되고 예외는 삼킴
    store = get_user_store()
    assert store._pool is None


def test_require_pool_raises_503_when_not_initialized():
    store = UserStore(pool=None)
    with pytest.raises(HTTPException) as exc:
        store._require_pool()
    assert exc.value.status_code == 503


# ─────────────────────────────────────────
# Subjects
# ─────────────────────────────────────────

async def test_get_subjects_returns_rows_as_dicts():
    rows = [{"id": "s1", "name": "수학", "color": "#fff", "created_at": None}]
    conn = _make_conn(fetch=rows)
    store = UserStore(_make_pool(conn))
    result = await store.get_subjects("user-1")
    assert result == [{"id": "s1", "name": "수학", "color": "#fff", "created_at": None}]
    conn.fetch.assert_awaited_once()


async def test_create_subject_returns_inserted_row():
    row = {"id": "s2", "name": "물리", "color": "#abc", "created_at": None}
    conn = _make_conn(fetchrow=row)
    store = UserStore(_make_pool(conn))
    result = await store.create_subject("user-1", "물리", "#abc")
    assert result["name"] == "물리"
    assert result["color"] == "#abc"


async def test_delete_subject_returns_true_on_match():
    conn = _make_conn(execute="DELETE 1")
    store = UserStore(_make_pool(conn))
    assert await store.delete_subject("user-1", "subj-1") is True


async def test_delete_subject_returns_false_when_no_match():
    conn = _make_conn(execute="DELETE 0")
    store = UserStore(_make_pool(conn))
    assert await store.delete_subject("user-1", "subj-1") is False


async def test_sync_subjects_returns_zero_for_empty_list():
    store = UserStore(_make_pool(AsyncMock()))
    assert await store.sync_subjects("user-1", []) == 0


async def test_sync_subjects_inserts_each():
    conn = _make_conn(execute="INSERT 0 1")
    store = UserStore(_make_pool(conn))
    subs = [
        SimpleNamespace(name="A", color="#111"),
        SimpleNamespace(name="B", color="#222"),
    ]
    n = await store.sync_subjects("user-1", subs)
    assert n == 2
    assert conn.execute.await_count == 2


# ─────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────

async def test_create_session_without_subject_skips_ownership_check():
    row = {"id": "sess-1", "pdf_name": "x.pdf", "pdf_hash": "h", "subject_id": None,
           "page_count": 1, "word_count": 1, "status": "pending",
           "created_at": None, "last_accessed": None}
    conn = _make_conn(fetchrow=row)
    store = UserStore(_make_pool(conn))
    body = SimpleNamespace(pdf_name="x.pdf", pdf_hash="h", subject_id=None,
                           page_count=1, word_count=1, status="pending")
    result = await store.create_session("user-1", body)
    assert result["id"] == "sess-1"
    # subject_id가 None이면 fetchval(ownership check)이 호출되지 않아야 함
    conn.fetchval.assert_not_called() if hasattr(conn.fetchval, 'assert_not_called') else None


async def test_create_session_with_subject_id_checks_ownership():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=1)  # 소유자임
    conn.fetchrow = AsyncMock(return_value={
        "id": "sess-1", "pdf_name": "x.pdf", "pdf_hash": "h",
        "subject_id": "subj-1", "page_count": 1, "word_count": 1,
        "status": "pending", "created_at": None, "last_accessed": None,
    })
    store = UserStore(_make_pool(conn))
    body = SimpleNamespace(pdf_name="x.pdf", pdf_hash="h", subject_id="subj-1",
                           page_count=1, word_count=1, status="pending")
    await store.create_session("user-1", body)
    conn.fetchval.assert_awaited_once()


async def test_create_session_subject_not_owned_raises_403():
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)  # 소유자 아님
    store = UserStore(_make_pool(conn))
    body = SimpleNamespace(pdf_name="x.pdf", pdf_hash="h", subject_id="other-subj",
                           page_count=1, word_count=1, status="pending")
    with pytest.raises(HTTPException) as exc:
        await store.create_session("user-1", body)
    assert exc.value.status_code == 403


async def test_upsert_session_falls_back_to_create_when_no_pdf_hash():
    """pdf_hash가 비어있으면 ON CONFLICT 키가 없으므로 일반 INSERT 경로로 전환."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={
        "id": "sess-X", "pdf_name": "x.pdf", "pdf_hash": "",
        "subject_id": None, "page_count": 1, "word_count": 1,
        "status": "pending", "created_at": None, "last_accessed": None,
    })
    store = UserStore(_make_pool(conn))
    body = SimpleNamespace(pdf_name="x.pdf", pdf_hash="", subject_id=None,
                           page_count=1, word_count=1, status="pending")
    result = await store.upsert_session("user-1", body)
    assert result["id"] == "sess-X"
    # fetchrow 한 번만 (create_session 경로)
    assert conn.fetchrow.await_count == 1


async def test_update_session_status_invalid_raises_value_error():
    store = UserStore(_make_pool(AsyncMock()))
    with pytest.raises(ValueError, match="invalid status"):
        await store.update_session_status("user-1", "sess-1", "weird-status")


async def test_update_session_status_returns_true_on_match():
    conn = _make_conn(execute="UPDATE 1")
    store = UserStore(_make_pool(conn))
    assert await store.update_session_status("user-1", "sess-1", "ready") is True


async def test_update_session_status_returns_false_when_zero_rows():
    conn = _make_conn(execute="UPDATE 0")
    store = UserStore(_make_pool(conn))
    assert await store.update_session_status("user-1", "sess-1", "ready") is False


async def test_delete_session_returns_true_on_match():
    conn = _make_conn(execute="DELETE 1")
    store = UserStore(_make_pool(conn))
    assert await store.delete_session("user-1", "sess-1") is True


async def test_delete_session_returns_false_when_no_match():
    conn = _make_conn(execute="DELETE 0")
    store = UserStore(_make_pool(conn))
    assert await store.delete_session("user-1", "sess-1") is False


async def test_sync_sessions_returns_zero_for_empty_list():
    store = UserStore(_make_pool(AsyncMock()))
    assert await store.sync_sessions("user-1", []) == 0


async def test_sync_sessions_invalid_subject_raises_403():
    """소유하지 않은 subject_id가 섞여 있으면 전체 sync 거부."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"id": "owned-subj"}])  # 소유한 것은 owned-subj만
    store = UserStore(_make_pool(conn))
    sessions = [SimpleNamespace(
        pdf_name="x", pdf_hash="h", subject_id="OTHER-subj",
        page_count=1, word_count=1, status="pending",
    )]
    with pytest.raises(HTTPException) as exc:
        await store.sync_sessions("user-1", sessions)
    assert exc.value.status_code == 403


# ─────────────────────────────────────────
# Reviews
# ─────────────────────────────────────────

async def test_get_due_reviews_returns_rows():
    rows = [{"id": "r1", "session_id": "s1", "question_id": "q1",
             "question_type": "mcq", "interval_days": 1,
             "next_review_at": None, "ease_factor": 2.5,
             "repetitions": 0, "status": "pending"}]
    conn = _make_conn(fetch=rows)
    store = UserStore(_make_pool(conn))
    result = await store.get_due_reviews("user-1")
    assert len(result) == 1
    assert result[0]["question_id"] == "q1"


async def test_upsert_review_returns_row():
    row = {"id": "r1", "session_id": "s1", "question_id": "q1",
           "question_type": "mcq", "interval_days": 1, "next_review_at": None,
           "ease_factor": 2.5, "repetitions": 0, "status": "pending"}
    conn = _make_conn(fetchrow=row)
    store = UserStore(_make_pool(conn))
    body = SimpleNamespace(
        session_id="s1", question_id="q1", question_type="mcq",
        interval_days=1, next_review_at=None, ease_factor=2.5,
        repetitions=0, status="pending",
    )
    result = await store.upsert_review("user-1", body)
    assert result["question_id"] == "q1"


async def test_sync_reviews_returns_zero_for_empty_list():
    store = UserStore(_make_pool(AsyncMock()))
    assert await store.sync_reviews("user-1", []) == 0


async def test_sync_reviews_inserts_each():
    conn = _make_conn(execute="INSERT 0 1")
    store = UserStore(_make_pool(conn))
    reviews = [SimpleNamespace(
        session_id="s1", question_id=f"q{i}", question_type="mcq",
        interval_days=1, next_review_at=None, ease_factor=2.5,
        repetitions=0, status="pending",
    ) for i in range(3)]
    n = await store.sync_reviews("user-1", reviews)
    assert n == 3
    assert conn.execute.await_count == 3
