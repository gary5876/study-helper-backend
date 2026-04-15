"""Unit tests for the question_bank service."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.question_bank as qb


@pytest.fixture(autouse=True)
def reset_pool():
    """Ensure _pool is reset to None before and after each test."""
    original = qb._pool
    qb._pool = None
    yield
    qb._pool = original


# ─────────────────────────────────────────
# Fallback behavior — pool is None (DB unavailable)
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_cached_returns_none_when_pool_is_none():
    result = await qb.get_cached("abc123")
    assert result is None


@pytest.mark.asyncio
async def test_get_cached_returns_none_for_empty_hash():
    result = await qb.get_cached("")
    assert result is None


@pytest.mark.asyncio
async def test_save_to_bank_returns_false_when_pool_is_none():
    """Pool 없음 = best-effort 캐시 저장 실패. False 반환 (예외 아님)."""
    result = await qb.save_to_bank("abc123", "doc.pdf", 5, 200, '{"ok":true}')
    assert result is False


@pytest.mark.asyncio
async def test_save_to_bank_returns_false_for_empty_hash():
    result = await qb.save_to_bank("", "doc.pdf", 5, 200, '{"ok":true}')
    assert result is False


# ─────────────────────────────────────────
# Happy path — pool available (mocked)
# ─────────────────────────────────────────

def _make_mock_pool(fetchrow_return=None, execute_raises=False):
    """Build a minimal asyncpg pool mock."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_return)
    if execute_raises:
        conn.execute = AsyncMock(side_effect=Exception("DB error"))
    else:
        conn.execute = AsyncMock(return_value=None)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AsyncContextManager(conn))
    return pool, conn


class _AsyncContextManager:
    """Helper: turns a value into an async context manager."""
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *args):
        pass


@pytest.mark.asyncio
async def test_get_cached_returns_content_on_hit():
    row = {"content_json": '{"session_id":"s1"}'}
    pool, conn = _make_mock_pool(fetchrow_return=row)
    qb._pool = pool

    result = await qb.get_cached("deadbeef")

    assert result == '{"session_id":"s1"}'
    conn.fetchrow.assert_awaited_once()
    conn.execute.assert_awaited_once()  # hit_count increment


@pytest.mark.asyncio
async def test_get_cached_returns_none_on_miss():
    pool, conn = _make_mock_pool(fetchrow_return=None)
    qb._pool = pool

    result = await qb.get_cached("deadbeef")

    assert result is None
    conn.execute.assert_not_awaited()  # no hit_count increment


@pytest.mark.asyncio
async def test_save_to_bank_executes_insert():
    pool, conn = _make_mock_pool()
    qb._pool = pool

    result = await qb.save_to_bank("deadbeef", "notes.pdf", 10, 500, '{"ok":true}')

    assert result is True
    conn.execute.assert_awaited_once()
    call_args = conn.execute.call_args[0]
    assert "INSERT INTO question_bank" in call_args[0]
    assert "deadbeef" in call_args


# ─────────────────────────────────────────
# Error handling — DB errors must not propagate
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_cached_swallows_db_error():
    pool, conn = _make_mock_pool()
    conn.fetchrow = AsyncMock(side_effect=Exception("connection lost"))
    qb._pool = pool

    result = await qb.get_cached("deadbeef")
    assert result is None  # error swallowed, returns None


@pytest.mark.asyncio
async def test_save_to_bank_swallows_db_error_and_returns_false():
    """DB 예외는 호출자에게 전파되지 않아야 하지만, 이제 False를 반환해
    관측(logger.error)이 가능해야 한다."""
    pool, conn = _make_mock_pool()
    conn.execute = AsyncMock(side_effect=Exception("connection lost"))
    qb._pool = pool

    result = await qb.save_to_bank("deadbeef", "doc.pdf", 5, 200, '{"ok":true}')
    assert result is False
