"""Unit tests for generate.py helpers and the _run_generation pipeline.

기존 test_generate_router.py가 라우터 입출력만 다뤘다면, 여기서는:
- _check_ownership / _sync_user_session_status / _load_result_from_db 단위 테스트
- _run_generation의 주요 경로(캐시 히트, 정상 파이프라인, 각 단계 실패) 통합 테스트
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.routers.generate import (
    _check_ownership,
    _load_result_from_db,
    _run_generation,
    _sync_user_session_status,
)
from app.services.session_store import SessionRecord, init_store, get_store


# ─────────────────────────────────────────
# _check_ownership
# ─────────────────────────────────────────

def _record(user_id=None):
    return SessionRecord(
        session_id="sess-1",
        pdf_name="x.pdf",
        page_count=1,
        word_count=10,
        s3_key="local/sess-1.pdf",
        pdf_hash="h",
        user_id=user_id,
        status="uploaded",
        progress_pct=0,
    )


def test_check_ownership_guest_session_allows_guest():
    _check_ownership(_record(user_id=None), user=None)  # no exception


def test_check_ownership_guest_session_with_user_raises_403():
    with pytest.raises(HTTPException) as exc:
        _check_ownership(_record(user_id=None), user={"user_id": "u1"})
    assert exc.value.status_code == 403


def test_check_ownership_owned_session_other_user_raises_403():
    with pytest.raises(HTTPException) as exc:
        _check_ownership(_record(user_id="owner"), user={"user_id": "intruder"})
    assert exc.value.status_code == 403


def test_check_ownership_owned_session_no_user_raises_403():
    with pytest.raises(HTTPException) as exc:
        _check_ownership(_record(user_id="owner"), user=None)
    assert exc.value.status_code == 403


def test_check_ownership_owned_session_correct_user_allowed():
    _check_ownership(_record(user_id="owner"), user={"user_id": "owner"})


# ─────────────────────────────────────────
# _sync_user_session_status
# ─────────────────────────────────────────

async def test_sync_status_no_user_returns_true():
    assert await _sync_user_session_status(None, "sess-1", "ready") is True


async def test_sync_status_db_exception_returns_false():
    fake_store = MagicMock()
    fake_store.update_session_status = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        assert await _sync_user_session_status("u1", "sess-1", "ready") is False


async def test_sync_status_zero_rows_returns_false():
    fake_store = MagicMock()
    fake_store.update_session_status = AsyncMock(return_value=False)
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        assert await _sync_user_session_status("u1", "sess-1", "ready") is False


async def test_sync_status_success_returns_true():
    fake_store = MagicMock()
    fake_store.update_session_status = AsyncMock(return_value=True)
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        assert await _sync_user_session_status("u1", "sess-1", "ready") is True


# ─────────────────────────────────────────
# _load_result_from_db
# ─────────────────────────────────────────

class _AcquireCM:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return None


async def test_load_result_from_db_no_pool_returns_none():
    fake_store = MagicMock()
    fake_store._pool = None
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        result = await _load_result_from_db("sess-1", "u1")
    assert result is None


async def test_load_result_from_db_session_not_found_returns_none():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCM(conn))
    fake_store = MagicMock()
    fake_store._pool = pool
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        result = await _load_result_from_db("sess-1", "u1")
    assert result is None


async def test_load_result_from_db_returns_cached():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value={"pdf_hash": "abc", "status": "ready"})
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCM(conn))
    fake_store = MagicMock()
    fake_store._pool = pool
    with patch("app.routers.generate.get_user_store", return_value=fake_store), \
         patch("app.routers.generate.get_cached", new=AsyncMock(return_value='{"x":1}')):
        result = await _load_result_from_db("sess-1", "u1")
    assert result == '{"x":1}'


async def test_load_result_from_db_swallows_exception():
    fake_store = MagicMock()
    fake_store._pool = MagicMock()
    fake_store._pool.acquire = MagicMock(side_effect=RuntimeError("boom"))
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        result = await _load_result_from_db("sess-1", "u1")
    assert result is None


# ─────────────────────────────────────────
# _run_generation — main pipeline
# ─────────────────────────────────────────

@pytest.fixture
async def memory_store():
    await init_store(redis_url=None)
    return get_store()


_TEST_COUNTER = {"n": 0}


async def _seed_record(store, *, parsed_text="Sample text.", user_id=None, pdf_hash="hash-1"):
    """uploaded 상태의 record를 store에 넣고 session_id 반환. 테스트마다 unique UUID."""
    _TEST_COUNTER["n"] += 1
    sid = f"00000000-0000-0000-0000-{_TEST_COUNTER['n']:012d}"
    parsed = {"full_text": parsed_text, "sections": [{"title": "S", "content": parsed_text}]}
    record = SessionRecord(
        session_id=sid,
        pdf_name="x.pdf",
        page_count=1,
        word_count=max(len(parsed_text.split()), 1),
        s3_key=f"local/{sid}.pdf",
        pdf_hash=pdf_hash,
        user_id=user_id,
        status="uploaded",
        progress_pct=0,
        result_json=json.dumps(parsed),
    )
    await store.save(record)
    return sid


async def test_run_generation_session_not_found_returns_early(memory_store):
    # store에 없는 session_id 호출 → 조용히 종료, 예외 안 나면 통과
    await _run_generation("00000000-0000-0000-0000-000000009999", "sk-ant-key", None, "paid", "ko")


async def test_run_generation_cache_hit_finalizes_ready(memory_store):
    sid = await _seed_record(memory_store)
    cached = '{"session_id":"' + sid + '","notes":{"key_concepts":[],"sections":[],"glossary":[]},' \
             '"mcq_questions":[],"fill_questions":[],"metadata":{"page_count":1,"word_count":1,' \
             '"generated_at":"2026-04-30T00:00:00+00:00","model_used":"claude","section_count":1}}'
    with patch("app.routers.generate.get_cached", new=AsyncMock(return_value=cached)), \
         patch("app.routers.generate._sync_user_session_status",
               new=AsyncMock(return_value=True)):
        await _run_generation(sid, "sk-ant-key", None, "paid", "ko")
    rec = await memory_store.get(sid)
    assert rec.status == "complete"
    assert rec.progress_pct == 100


async def test_run_generation_empty_text_fails(memory_store):
    sid = await _seed_record(memory_store, parsed_text="")
    with patch("app.routers.generate.get_cached", new=AsyncMock(return_value=None)):
        await _run_generation(sid, "sk-ant-key", None, "paid", "ko")
    rec = await memory_store.get(sid)
    assert rec.status == "failed"
    assert "empty" in (rec.error_message or "").lower()


async def test_run_generation_invalid_parsed_json_fails(memory_store):
    """result_json에 JSON 깨진 문자열이 들어가면 _fail로 빠짐."""
    _TEST_COUNTER["n"] += 1
    sid = f"00000000-0000-0000-0000-{_TEST_COUNTER['n']:012d}"
    record = SessionRecord(
        session_id=sid,
        pdf_name="x.pdf",
        page_count=1, word_count=1,
        s3_key="local/x.pdf",
        pdf_hash="h",
        user_id=None,
        status="uploaded", progress_pct=0,
        result_json="{not valid json",
    )
    await memory_store.save(record)
    with patch("app.routers.generate.get_cached", new=AsyncMock(return_value=None)):
        await _run_generation(sid, "sk-ant-key", None, "paid", "ko")
    rec = await memory_store.get(sid)
    assert rec.status == "failed"


async def test_run_generation_notes_failure_marks_failed(memory_store):
    sid = await _seed_record(memory_store)
    from app.core.exceptions import GenerationError
    with patch("app.routers.generate.get_cached", new=AsyncMock(return_value=None)), \
         patch("app.routers.generate.anthropic_generate",
               new=AsyncMock(side_effect=GenerationError("API down"))):
        await _run_generation(sid, "sk-ant-key", None, "paid", "ko")
    rec = await memory_store.get(sid)
    assert rec.status == "failed"
    assert "Notes generation failed" in (rec.error_message or "")
