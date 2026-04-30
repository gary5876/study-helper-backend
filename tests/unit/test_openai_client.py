"""Unit tests for app/services/openai_client.py.

anthropic_client와 구조 동일 — _call_gpt 에러 경로별 status_code 매핑 + retry 동작 검증.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

from app.core.exceptions import GenerationError
from app.services.openai_client import (
    _call_gpt,
    _circuit_breaker,
    generate_with_retry,
)


@pytest.fixture(autouse=True)
def _reset_global_circuit_breaker():
    _circuit_breaker._failures = 0
    _circuit_breaker._opened_at = None
    yield
    _circuit_breaker._failures = 0
    _circuit_breaker._opened_at = None


def _make_openai_exc(exc_class, **attrs):
    exc = exc_class.__new__(exc_class)
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


def _patch_client(monkeypatch, *, side_effect=None, return_text=None):
    mock_completions = MagicMock()
    if side_effect is not None:
        mock_completions.create = AsyncMock(side_effect=side_effect)
    else:
        mock_choice = MagicMock()
        mock_choice.message = MagicMock(content=return_text or "default")
        mock_response = MagicMock()
        mock_response.choices = [mock_choice]
        mock_completions.create = AsyncMock(return_value=mock_response)
    mock_chat = MagicMock()
    mock_chat.completions = mock_completions
    mock_client = MagicMock()
    mock_client.chat = mock_chat
    monkeypatch.setattr(
        "app.services.openai_client.openai.AsyncOpenAI",
        MagicMock(return_value=mock_client),
    )


# ─────────────────────────────────────────
# _call_gpt 경로별 검증
# ─────────────────────────────────────────

async def test_call_gpt_returns_text(monkeypatch):
    _patch_client(monkeypatch, return_text='{"hello":"world"}')
    result = await _call_gpt("sk-test", "sys", "user")
    assert result == '{"hello":"world"}'


async def test_call_gpt_returns_empty_string_when_content_is_none(monkeypatch):
    """OpenAI가 message.content=None을 반환하면 빈 문자열 fallback."""
    mock_choice = MagicMock()
    mock_choice.message = MagicMock(content=None)
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_completions = MagicMock()
    mock_completions.create = AsyncMock(return_value=mock_response)
    mock_chat = MagicMock()
    mock_chat.completions = mock_completions
    mock_client = MagicMock()
    mock_client.chat = mock_chat
    monkeypatch.setattr(
        "app.services.openai_client.openai.AsyncOpenAI",
        MagicMock(return_value=mock_client),
    )
    result = await _call_gpt("sk-test", "sys", "user")
    assert result == ""


async def test_call_gpt_circuit_open_raises_503():
    _circuit_breaker._failures = _circuit_breaker.FAILURE_THRESHOLD
    _circuit_breaker._opened_at = __import__("time").monotonic()
    with pytest.raises(GenerationError) as exc:
        await _call_gpt("sk-test", "sys", "user")
    assert exc.value.status_code == 503


async def test_call_gpt_auth_error_raises_401(monkeypatch):
    auth_exc = _make_openai_exc(openai.AuthenticationError, message="bad key", status_code=401)
    _patch_client(monkeypatch, side_effect=auth_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_gpt("sk-test", "sys", "user")
    assert exc.value.status_code == 401
    # auth는 circuit breaker 카운트 안 됨
    assert _circuit_breaker._failures == 0


async def test_call_gpt_rate_limit_raises_429(monkeypatch):
    rl_exc = _make_openai_exc(openai.RateLimitError, message="rate", status_code=429)
    _patch_client(monkeypatch, side_effect=rl_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_gpt("sk-test", "sys", "user")
    assert exc.value.status_code == 429


async def test_call_gpt_quota_402_returns_503(monkeypatch):
    quota_exc = _make_openai_exc(openai.APIStatusError, message="quota", status_code=402)
    _patch_client(monkeypatch, side_effect=quota_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_gpt("sk-test", "sys", "user")
    assert exc.value.status_code == 503


async def test_call_gpt_quota_403_returns_503(monkeypatch):
    quota_exc = _make_openai_exc(openai.APIStatusError, message="forbidden", status_code=403)
    _patch_client(monkeypatch, side_effect=quota_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_gpt("sk-test", "sys", "user")
    assert exc.value.status_code == 503


async def test_call_gpt_5xx_status_records_failure_and_502(monkeypatch):
    api_exc = _make_openai_exc(openai.APIStatusError, message="server", status_code=500)
    _patch_client(monkeypatch, side_effect=api_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_gpt("sk-test", "sys", "user")
    assert exc.value.status_code == 502
    assert _circuit_breaker._failures == 1


async def test_call_gpt_timeout_returns_504(monkeypatch):
    t_exc = _make_openai_exc(openai.APITimeoutError, message="timeout")
    _patch_client(monkeypatch, side_effect=t_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_gpt("sk-test", "sys", "user")
    assert exc.value.status_code == 504


async def test_call_gpt_connection_error_returns_502(monkeypatch):
    c_exc = _make_openai_exc(openai.APIConnectionError, message="conn")
    _patch_client(monkeypatch, side_effect=c_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_gpt("sk-test", "sys", "user")
    assert exc.value.status_code == 502


# ─────────────────────────────────────────
# generate_with_retry 정책
# ─────────────────────────────────────────

async def test_gpt_retry_no_retry_on_401(monkeypatch):
    call_count = {"n": 0}

    async def _fail(*a, **kw):
        call_count["n"] += 1
        raise GenerationError("auth", status_code=401)

    monkeypatch.setattr("app.services.openai_client._call_gpt", _fail)
    with pytest.raises(GenerationError) as exc:
        await generate_with_retry("sk-test", "s", "u", max_retries=3)
    assert exc.value.status_code == 401
    assert call_count["n"] == 1


async def test_gpt_retry_succeeds_after_failure(monkeypatch):
    call_count = {"n": 0}

    async def _flaky(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise GenerationError("transient", status_code=500)
        return '{"ok": 1}'

    monkeypatch.setattr("app.services.openai_client._call_gpt", _flaky)
    monkeypatch.setattr("app.services.openai_client.asyncio.sleep", AsyncMock())
    result = await generate_with_retry("sk-test", "s", "u", max_retries=2)
    assert result == {"ok": 1}
    assert call_count["n"] == 2


async def test_gpt_retry_exhaustion_raises_500(monkeypatch):
    async def _always_fail(*a, **kw):
        raise GenerationError("flaky", status_code=500)

    monkeypatch.setattr("app.services.openai_client._call_gpt", _always_fail)
    monkeypatch.setattr("app.services.openai_client.asyncio.sleep", AsyncMock())
    with pytest.raises(GenerationError) as exc:
        await generate_with_retry("sk-test", "s", "u", max_retries=1)
    assert exc.value.status_code == 500


async def test_gpt_retry_invokes_on_attempt(monkeypatch):
    monkeypatch.setattr(
        "app.services.openai_client._call_gpt", AsyncMock(return_value='{"ok":1}'),
    )
    seen = []
    await generate_with_retry(
        "sk-test", "s", "u", on_attempt=lambda i: seen.append(i),
    )
    assert seen == [0]
