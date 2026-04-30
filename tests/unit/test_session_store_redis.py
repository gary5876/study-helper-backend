"""Unit tests for the Redis-backed branch of app/services/session_store.py.

기존 test_session_store.py는 in-memory fallback만 다루므로 Redis 분기 미커버.
aioredis 클라이언트는 AsyncMock으로 대체.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.session_store import SessionRecord, SessionStore


def _record(sid="sess-redis-1"):
    return SessionRecord(
        session_id=sid,
        pdf_name="x.pdf",
        page_count=2,
        word_count=10,
        s3_key=f"uploads/{sid}/x.pdf",
        pdf_hash="h",
        status="uploaded",
        progress_pct=0,
    )


def _store_with_redis(redis_mock):
    store = SessionStore(redis_url="redis://fake")
    store._redis = redis_mock
    return store


# ─────────────────────────────────────────
# connect()
# ─────────────────────────────────────────

async def test_connect_succeeds_when_redis_available(monkeypatch):
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.services.session_store._REDIS_AVAILABLE", True,
    )
    monkeypatch.setattr(
        "app.services.session_store.aioredis.from_url",
        MagicMock(return_value=fake_redis),
    )
    store = SessionStore(redis_url="redis://fake")
    await store.connect()
    assert store._redis is fake_redis
    fake_redis.ping.assert_awaited_once()


async def test_connect_falls_back_to_memory_on_ping_failure(monkeypatch):
    fake_redis = AsyncMock()
    fake_redis.ping = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr("app.services.session_store._REDIS_AVAILABLE", True)
    monkeypatch.setattr(
        "app.services.session_store.aioredis.from_url",
        MagicMock(return_value=fake_redis),
    )
    store = SessionStore(redis_url="redis://fake")
    await store.connect()
    # 실패 시 _redis를 None으로 되돌리고 메모리 fallback 진행
    assert store._redis is None


async def test_connect_skips_when_no_url():
    """redis_url 없으면 connect() no-op."""
    store = SessionStore(redis_url=None)
    await store.connect()
    assert store._redis is None


# ─────────────────────────────────────────
# save / get / update_status / delete via Redis
# ─────────────────────────────────────────

async def test_save_calls_setex_with_ttl():
    fake_redis = AsyncMock()
    fake_redis.setex = AsyncMock()
    store = _store_with_redis(fake_redis)
    rec = _record()
    await store.save(rec)
    fake_redis.setex.assert_awaited_once()
    args = fake_redis.setex.call_args.args
    assert args[0] == f"session:{rec.session_id}"
    assert args[1] == SessionStore.TTL
    # 직렬화된 JSON에 session_id 포함
    assert rec.session_id in args[2]


async def test_get_returns_none_for_missing_key():
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    store = _store_with_redis(fake_redis)
    result = await store.get("nope")
    assert result is None


async def test_get_deserializes_record():
    fake_redis = AsyncMock()
    rec = _record("sid-from-redis")
    serialized = json.dumps({
        "session_id": rec.session_id,
        "pdf_name": rec.pdf_name,
        "page_count": rec.page_count,
        "word_count": rec.word_count,
        "s3_key": rec.s3_key,
        "pdf_hash": rec.pdf_hash,
        "user_id": None,
        "status": "uploaded",
        "progress_pct": 0,
        "error_message": None,
        "result_json": None,
    })
    fake_redis.get = AsyncMock(return_value=serialized)
    store = _store_with_redis(fake_redis)
    result = await store.get("sid-from-redis")
    assert isinstance(result, SessionRecord)
    assert result.session_id == "sid-from-redis"
    assert result.pdf_name == "x.pdf"


async def test_update_status_warns_on_unknown_session(caplog):
    fake_redis = AsyncMock()
    fake_redis.get = AsyncMock(return_value=None)
    store = _store_with_redis(fake_redis)
    # 예외 안 나야 함; 단지 logger.warning만 발생
    await store.update_status("nope", "complete")


async def test_update_status_persists_via_redis():
    fake_redis = AsyncMock()
    rec = _record("s-x")
    fake_redis.get = AsyncMock(return_value=json.dumps({
        "session_id": rec.session_id, "pdf_name": rec.pdf_name,
        "page_count": rec.page_count, "word_count": rec.word_count,
        "s3_key": rec.s3_key, "pdf_hash": rec.pdf_hash,
        "user_id": None, "status": "uploaded", "progress_pct": 0,
        "error_message": None, "result_json": None,
    }))
    fake_redis.setex = AsyncMock()
    store = _store_with_redis(fake_redis)
    await store.update_status("s-x", "complete", progress_pct=100, result_json='{"ok":1}')
    # save() → setex 호출
    fake_redis.setex.assert_awaited_once()


async def test_delete_returns_true_when_redis_deletes():
    fake_redis = AsyncMock()
    fake_redis.delete = AsyncMock(return_value=1)
    store = _store_with_redis(fake_redis)
    assert await store.delete("s-x") is True


async def test_delete_returns_false_when_redis_returns_zero():
    fake_redis = AsyncMock()
    fake_redis.delete = AsyncMock(return_value=0)
    store = _store_with_redis(fake_redis)
    assert await store.delete("nope") is False
