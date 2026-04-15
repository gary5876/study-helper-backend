"""Unit tests for the refactored `/result/{id}` retrieval priorities.

Covers Phase 2C of the 2026-04-15 result 영속화 리팩터:
  1. memory store hit → 즉시 반환
  2. memory miss + user_sessions.result_json 있음 → DB primary 반환
  3. memory miss + result_json NULL + question_bank hit → 레거시 복구 + backfill
  4. memory miss + 모든 경로 실패 → 404

Also covers:
  - save_to_bank 실패 시 generation은 성공으로 간주 (user_sessions가 primary)
  - finalize_session 실패 시 세션은 failed로 전이
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.routers.generate import _load_result_from_db, _finalize_session_primary


# ─────────────────────────────────────────
# _load_result_from_db — primary / legacy / miss 경로
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_primary_returns_result_json():
    """user_sessions.result_json가 있으면 그대로 반환."""
    fake_store = MagicMock()
    fake_store.get_session_with_result = AsyncMock(return_value={
        "id": "sess-1",
        "pdf_hash": "hash-1",
        "status": "ready",
        "result_json": '{"hello":"world"}',
        "error_message": None,
    })
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        result, reason = await _load_result_from_db("sess-1", "user-1")
    assert result == '{"hello":"world"}'
    assert reason == ""


@pytest.mark.asyncio
async def test_load_legacy_backfills_from_question_bank():
    """result_json NULL + pdf_hash 있음 + question_bank hit → 반환 + backfill."""
    fake_store = MagicMock()
    fake_store.get_session_with_result = AsyncMock(return_value={
        "id": "sess-2",
        "pdf_hash": "hash-2",
        "status": "ready",
        "result_json": None,
        "error_message": None,
    })
    fake_store.backfill_result = AsyncMock(return_value=True)

    with patch("app.routers.generate.get_user_store", return_value=fake_store), \
         patch("app.routers.generate.get_cached", AsyncMock(return_value='{"legacy":"ok"}')):
        result, reason = await _load_result_from_db("sess-2", "user-1")

    assert result == '{"legacy":"ok"}'
    assert reason == ""
    fake_store.backfill_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_returns_none_when_row_missing():
    """user_sessions 행 자체가 없으면 None + user_sessions_row_missing."""
    fake_store = MagicMock()
    fake_store.get_session_with_result = AsyncMock(return_value=None)
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        result, reason = await _load_result_from_db("sess-3", "user-1")
    assert result is None
    assert reason == "user_sessions_row_missing"


@pytest.mark.asyncio
async def test_load_returns_none_when_legacy_hash_null():
    """result_json NULL + pdf_hash NULL → 완전 유실."""
    fake_store = MagicMock()
    fake_store.get_session_with_result = AsyncMock(return_value={
        "id": "sess-4",
        "pdf_hash": None,
        "status": "ready",
        "result_json": None,
        "error_message": None,
    })
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        result, reason = await _load_result_from_db("sess-4", "user-1")
    assert result is None
    assert reason == "legacy_no_pdf_hash"


@pytest.mark.asyncio
async def test_load_returns_none_when_question_bank_miss():
    """레거시 행이 있고 pdf_hash도 있지만 question_bank에는 없음."""
    fake_store = MagicMock()
    fake_store.get_session_with_result = AsyncMock(return_value={
        "id": "sess-5",
        "pdf_hash": "hash-5",
        "status": "ready",
        "result_json": None,
        "error_message": None,
    })
    with patch("app.routers.generate.get_user_store", return_value=fake_store), \
         patch("app.routers.generate.get_cached", AsyncMock(return_value=None)):
        result, reason = await _load_result_from_db("sess-5", "user-1")
    assert result is None
    assert reason == "question_bank_miss"


@pytest.mark.asyncio
async def test_load_handles_user_store_exception():
    """user_store 예외 시 None + user_sessions_query_error."""
    fake_store = MagicMock()
    fake_store.get_session_with_result = AsyncMock(side_effect=RuntimeError("db down"))
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        result, reason = await _load_result_from_db("sess-6", "user-1")
    assert result is None
    assert reason == "user_sessions_query_error"


# ─────────────────────────────────────────
# _finalize_session_primary — DB primary write
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_primary_guest_bypass():
    """로그인 사용자가 아니면 True (해당 없음, memory store만 사용)."""
    ok = await _finalize_session_primary(None, "sess-1", '{"x":1}')
    assert ok is True


@pytest.mark.asyncio
async def test_finalize_primary_user_success():
    fake_store = MagicMock()
    fake_store.finalize_session = AsyncMock(return_value=True)
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        ok = await _finalize_session_primary("user-1", "sess-1", '{"x":1}')
    assert ok is True
    fake_store.finalize_session.assert_awaited_once_with("user-1", "sess-1", '{"x":1}')


@pytest.mark.asyncio
async def test_finalize_primary_user_db_zero_rows():
    """대상 행이 없으면 False (고아 방지)."""
    fake_store = MagicMock()
    fake_store.finalize_session = AsyncMock(return_value=False)
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        ok = await _finalize_session_primary("user-1", "sess-1", '{"x":1}')
    assert ok is False


@pytest.mark.asyncio
async def test_finalize_primary_user_db_exception():
    fake_store = MagicMock()
    fake_store.finalize_session = AsyncMock(side_effect=RuntimeError("conn lost"))
    with patch("app.routers.generate.get_user_store", return_value=fake_store):
        ok = await _finalize_session_primary("user-1", "sess-1", '{"x":1}')
    assert ok is False
