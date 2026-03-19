"""Unit tests for the SessionStore (in-memory mode)."""
from __future__ import annotations

import pytest

from app.services.session_store import SessionRecord, SessionStore, init_store, get_store


@pytest.fixture
async def store():
    """Fresh in-memory store for each test."""
    s = await init_store(redis_url=None)
    yield s


def _make_record(session_id: str = "sess-1") -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        pdf_name="test.pdf",
        page_count=5,
        word_count=200,
        s3_key=f"uploads/{session_id}/test.pdf",
    )


# ─────────────────────────────────────────
# save / get
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_get(store):
    rec = _make_record("abc")
    await store.save(rec)
    fetched = await store.get("abc")
    assert fetched is not None
    assert fetched.session_id == "abc"
    assert fetched.pdf_name == "test.pdf"


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    result = await store.get("nonexistent")
    assert result is None


# ─────────────────────────────────────────
# update_status
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_status_processing(store):
    await store.save(_make_record("s1"))
    await store.update_status("s1", "processing", progress_pct=50)
    rec = await store.get("s1")
    assert rec.status == "processing"
    assert rec.progress_pct == 50


@pytest.mark.asyncio
async def test_update_status_complete_with_result(store):
    await store.save(_make_record("s2"))
    await store.update_status("s2", "complete", progress_pct=100, result_json='{"ok":true}')
    rec = await store.get("s2")
    assert rec.status == "complete"
    assert rec.result_json == '{"ok":true}'


@pytest.mark.asyncio
async def test_update_status_failed_with_error(store):
    await store.save(_make_record("s3"))
    await store.update_status("s3", "failed", error_message="Something broke")
    rec = await store.get("s3")
    assert rec.status == "failed"
    assert rec.error_message == "Something broke"


@pytest.mark.asyncio
async def test_update_status_unknown_session_does_not_raise(store):
    # Should log a warning but not raise
    await store.update_status("no-such-session", "processing", progress_pct=10)


# ─────────────────────────────────────────
# delete
# ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_existing(store):
    await store.save(_make_record("d1"))
    deleted = await store.delete("d1")
    assert deleted is True
    assert await store.get("d1") is None


@pytest.mark.asyncio
async def test_delete_nonexistent(store):
    deleted = await store.delete("ghost")
    assert deleted is False


# ─────────────────────────────────────────
# get_store raises before init
# ─────────────────────────────────────────

def test_get_store_before_init_raises(monkeypatch):
    import app.services.session_store as ss
    original = ss._store
    ss._store = None
    with pytest.raises(RuntimeError, match="not initialised"):
        get_store()
    ss._store = original
