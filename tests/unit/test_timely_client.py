"""Unit tests for app/services/timely_client.py.

다른 client와 달리 다음을 추가로 다룸:
- _get_access_token: cache hit/miss/만료, 401, network 실패, no-token 응답
- _call_timely: 401 시 캐시 evict, 429, 402/403 quota, unexpected 응답 type
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.core.exceptions import GenerationError
from app.services import timely_client
from app.services.timely_client import (
    _call_timely,
    _circuit_breaker,
    _get_access_token,
    _token_cache,
    generate_with_retry,
)


@pytest.fixture(autouse=True)
def _reset_state():
    _token_cache.clear()
    _circuit_breaker._failures = 0
    _circuit_breaker._opened_at = None
    yield
    _token_cache.clear()
    _circuit_breaker._failures = 0
    _circuit_breaker._opened_at = None


# ─────────────────────────────────────────
# httpx.AsyncClient mock helper
# ─────────────────────────────────────────

class _FakeAsyncClientCM:
    """async with httpx.AsyncClient() as client: ... 패턴 모킹."""
    def __init__(self, get_response=None, post_response=None,
                 get_exc=None, post_exc=None):
        self._get_response = get_response
        self._post_response = post_response
        self._get_exc = get_exc
        self._post_exc = post_exc

    async def __aenter__(self):
        client = MagicMock()
        if self._get_exc:
            client.get = AsyncMock(side_effect=self._get_exc)
        else:
            client.get = AsyncMock(return_value=self._get_response)
        if self._post_exc:
            client.post = AsyncMock(side_effect=self._post_exc)
        else:
            client.post = AsyncMock(return_value=self._post_response)
        return client

    async def __aexit__(self, *args):
        return None


def _resp(status_code=200, json_body=None, is_success=None):
    r = MagicMock()
    r.status_code = status_code
    r.is_success = is_success if is_success is not None else (200 <= status_code < 300)
    r.json = MagicMock(return_value=json_body or {})
    return r


def _patch_client(monkeypatch, **kwargs):
    monkeypatch.setattr(
        "app.services.timely_client.httpx.AsyncClient",
        lambda: _FakeAsyncClientCM(**kwargs),
    )


# ─────────────────────────────────────────
# _get_access_token
# ─────────────────────────────────────────

async def test_get_access_token_uses_cache_when_fresh(monkeypatch):
    _token_cache["k1"] = ("cached-token", time.monotonic() + 100)
    # httpx.AsyncClient가 호출되지 않아야 함 — 호출되면 명시적으로 실패
    monkeypatch.setattr(
        "app.services.timely_client.httpx.AsyncClient",
        MagicMock(side_effect=AssertionError("should not call httpx for cache hit")),
    )
    result = await _get_access_token("k1")
    assert result == "cached-token"


async def test_get_access_token_refetches_when_expired(monkeypatch):
    _token_cache["k1"] = ("old-token", time.monotonic() - 1)  # 만료됨
    _patch_client(monkeypatch, get_response=_resp(
        200, {"data": {"access_token": "new-token"}},
    ))
    result = await _get_access_token("k1")
    assert result == "new-token"
    assert _token_cache["k1"][0] == "new-token"


async def test_get_access_token_401_raises(monkeypatch):
    _patch_client(monkeypatch, get_response=_resp(401, {}))
    with pytest.raises(GenerationError) as exc:
        await _get_access_token("k-bad")
    assert exc.value.status_code == 401


async def test_get_access_token_5xx_raises_502(monkeypatch):
    _patch_client(monkeypatch, get_response=_resp(500, {}, is_success=False))
    with pytest.raises(GenerationError) as exc:
        await _get_access_token("k1")
    assert exc.value.status_code == 502


async def test_get_access_token_no_token_in_response_raises(monkeypatch):
    _patch_client(monkeypatch, get_response=_resp(200, {"data": {}}))
    with pytest.raises(GenerationError) as exc:
        await _get_access_token("k1")
    assert exc.value.status_code == 502


async def test_get_access_token_timeout_raises_504(monkeypatch):
    _patch_client(monkeypatch, get_exc=httpx.TimeoutException("slow"))
    with pytest.raises(GenerationError) as exc:
        await _get_access_token("k1")
    assert exc.value.status_code == 504


async def test_get_access_token_request_error_raises_502(monkeypatch):
    _patch_client(monkeypatch, get_exc=httpx.RequestError("conn refused"))
    with pytest.raises(GenerationError) as exc:
        await _get_access_token("k1")
    assert exc.value.status_code == 502


# ─────────────────────────────────────────
# _call_timely
# ─────────────────────────────────────────

async def test_call_timely_returns_message(monkeypatch):
    _token_cache["k1"] = ("tok", time.monotonic() + 100)
    _patch_client(monkeypatch, post_response=_resp(
        200, {"type": "final_response", "message": "the answer"},
    ))
    result = await _call_timely("k1", "sys", "user")
    assert result == "the answer"


async def test_call_timely_circuit_open_raises_503():
    _circuit_breaker._failures = _circuit_breaker.FAILURE_THRESHOLD
    _circuit_breaker._opened_at = time.monotonic()
    with pytest.raises(GenerationError) as exc:
        await _call_timely("k1", "sys", "user")
    assert exc.value.status_code == 503


async def test_call_timely_401_evicts_token_cache(monkeypatch):
    _token_cache["k1"] = ("tok", time.monotonic() + 100)
    _patch_client(monkeypatch, post_response=_resp(401, {}))
    with pytest.raises(GenerationError) as exc:
        await _call_timely("k1", "sys", "user")
    assert exc.value.status_code == 401
    assert "k1" not in _token_cache


async def test_call_timely_429_raises(monkeypatch):
    _token_cache["k1"] = ("tok", time.monotonic() + 100)
    _patch_client(monkeypatch, post_response=_resp(429, {}))
    with pytest.raises(GenerationError) as exc:
        await _call_timely("k1", "sys", "user")
    assert exc.value.status_code == 429


async def test_call_timely_402_quota_raises_503(monkeypatch):
    _token_cache["k1"] = ("tok", time.monotonic() + 100)
    _patch_client(monkeypatch, post_response=_resp(402, {}))
    with pytest.raises(GenerationError) as exc:
        await _call_timely("k1", "sys", "user")
    assert exc.value.status_code == 503


async def test_call_timely_5xx_records_failure(monkeypatch):
    _token_cache["k1"] = ("tok", time.monotonic() + 100)
    _patch_client(monkeypatch, post_response=_resp(500, {}, is_success=False))
    with pytest.raises(GenerationError) as exc:
        await _call_timely("k1", "sys", "user")
    assert exc.value.status_code == 502
    assert _circuit_breaker._failures == 1


async def test_call_timely_timeout_returns_504(monkeypatch):
    _token_cache["k1"] = ("tok", time.monotonic() + 100)
    _patch_client(monkeypatch, post_exc=httpx.TimeoutException("slow"))
    with pytest.raises(GenerationError) as exc:
        await _call_timely("k1", "sys", "user")
    assert exc.value.status_code == 504


async def test_call_timely_connection_error_returns_502(monkeypatch):
    _token_cache["k1"] = ("tok", time.monotonic() + 100)
    _patch_client(monkeypatch, post_exc=httpx.RequestError("conn"))
    with pytest.raises(GenerationError) as exc:
        await _call_timely("k1", "sys", "user")
    assert exc.value.status_code == 502


async def test_call_timely_unexpected_response_type_raises(monkeypatch):
    _token_cache["k1"] = ("tok", time.monotonic() + 100)
    _patch_client(monkeypatch, post_response=_resp(
        200, {"type": "intermediate_event"},
    ))
    with pytest.raises(GenerationError) as exc:
        await _call_timely("k1", "sys", "user")
    assert exc.value.status_code == 502


# ─────────────────────────────────────────
# generate_with_retry
# ─────────────────────────────────────────

async def test_timely_retry_no_retry_on_503(monkeypatch):
    call_count = {"n": 0}

    async def _fail(*a, **kw):
        call_count["n"] += 1
        raise GenerationError("quota", status_code=503)

    monkeypatch.setattr("app.services.timely_client._call_timely", _fail)
    with pytest.raises(GenerationError) as exc:
        await generate_with_retry("k1", "s", "u", max_retries=3)
    assert exc.value.status_code == 503
    assert call_count["n"] == 1


async def test_timely_retry_succeeds_after_failure(monkeypatch):
    call_count = {"n": 0}

    async def _flaky(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise GenerationError("transient", status_code=500)
        return '{"ok": true}'

    monkeypatch.setattr("app.services.timely_client._call_timely", _flaky)
    monkeypatch.setattr("app.services.timely_client.asyncio.sleep", AsyncMock())
    result = await generate_with_retry("k1", "s", "u", max_retries=2)
    assert result == {"ok": True}
    assert call_count["n"] == 2


async def test_timely_retry_exhaustion_raises_500(monkeypatch):
    async def _always_fail(*a, **kw):
        raise GenerationError("flaky", status_code=500)

    monkeypatch.setattr("app.services.timely_client._call_timely", _always_fail)
    monkeypatch.setattr("app.services.timely_client.asyncio.sleep", AsyncMock())
    with pytest.raises(GenerationError) as exc:
        await generate_with_retry("k1", "s", "u", max_retries=1)
    assert exc.value.status_code == 500


async def test_timely_retry_invokes_on_attempt(monkeypatch):
    monkeypatch.setattr(
        "app.services.timely_client._call_timely", AsyncMock(return_value='{"ok":1}'),
    )
    seen = []
    await generate_with_retry(
        "k1", "s", "u", on_attempt=lambda i: seen.append(i),
    )
    assert seen == [0]
