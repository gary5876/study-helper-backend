"""Extended tests for app/services/anthropic_client.py.

기존 test_anthropic_client.py는 extract_json + retry 흐름 일부만 다룸.
여기서는 _CircuitBreaker, _call_claude 모든 에러 경로, generate_study_content를 커버.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

from app.core.exceptions import GenerationError
from app.services.anthropic_client import (
    _CircuitBreaker,
    _call_claude,
    _circuit_breaker,
    generate_study_content,
    generate_with_retry,
)


# ─────────────────────────────────────────
# 글로벌 circuit breaker 리셋 — 테스트 간섭 방지
# ─────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_global_circuit_breaker():
    _circuit_breaker._failures = 0
    _circuit_breaker._opened_at = None
    yield
    _circuit_breaker._failures = 0
    _circuit_breaker._opened_at = None


# ─────────────────────────────────────────
# _CircuitBreaker
# ─────────────────────────────────────────

def test_circuit_breaker_starts_closed():
    cb = _CircuitBreaker()
    assert cb.is_open is False


def test_circuit_breaker_opens_after_threshold():
    cb = _CircuitBreaker()
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure()
    assert cb.is_open is True
    assert cb._opened_at is not None


def test_circuit_breaker_below_threshold_stays_closed():
    cb = _CircuitBreaker()
    for _ in range(cb.FAILURE_THRESHOLD - 1):
        cb.record_failure()
    assert cb.is_open is False


def test_circuit_breaker_record_success_resets_failures():
    cb = _CircuitBreaker()
    for _ in range(3):
        cb.record_failure()
    cb.record_success()
    assert cb._failures == 0
    assert cb._opened_at is None


def test_circuit_breaker_recovers_after_timeout(monkeypatch):
    """RECOVERY_TIMEOUT 경과 후 is_open이 False로 전환되며 _opened_at도 None."""
    cb = _CircuitBreaker()
    for _ in range(cb.FAILURE_THRESHOLD):
        cb.record_failure()
    # 회복 시점을 지나도록 monotonic 모킹
    monkeypatch.setattr(
        "app.services.anthropic_client.time.monotonic",
        lambda: (cb._opened_at or 0) + cb.RECOVERY_TIMEOUT + 1,
    )
    assert cb.is_open is False
    assert cb._opened_at is None  # half-open: 다음 호출 허용


# ─────────────────────────────────────────
# _call_claude — 에러 경로별 status_code 매핑
# ─────────────────────────────────────────

def _make_anthropic_exc(exc_class, **attrs):
    """anthropic SDK 예외 인스턴스 생성 (생성자 시그니처 다양해서 __new__ 우회)."""
    exc = exc_class.__new__(exc_class)
    for k, v in attrs.items():
        setattr(exc, k, v)
    return exc


def _patch_client(monkeypatch, *, side_effect=None, return_text=None):
    mock_messages = MagicMock()
    if side_effect is not None:
        mock_messages.create = AsyncMock(side_effect=side_effect)
    else:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=return_text or "default")]
        mock_messages.create = AsyncMock(return_value=mock_msg)
    mock_client = MagicMock()
    mock_client.messages = mock_messages
    monkeypatch.setattr(
        "app.services.anthropic_client.anthropic.AsyncAnthropic",
        MagicMock(return_value=mock_client),
    )


async def test_call_claude_returns_text(monkeypatch):
    _patch_client(monkeypatch, return_text="hello world")
    result = await _call_claude("sk-ant-x", "sys", "user")
    assert result == "hello world"


async def test_call_claude_circuit_open_raises_503(monkeypatch):
    # circuit breaker를 강제로 open 상태로
    _circuit_breaker.record_failure()
    _circuit_breaker._opened_at = __import__("time").monotonic()
    _circuit_breaker._failures = _circuit_breaker.FAILURE_THRESHOLD
    with pytest.raises(GenerationError) as exc:
        await _call_claude("sk-ant-x", "sys", "user")
    assert exc.value.status_code == 503


async def test_call_claude_auth_error_raises_401(monkeypatch):
    auth_exc = _make_anthropic_exc(anthropic.AuthenticationError, message="bad key", status_code=401)
    _patch_client(monkeypatch, side_effect=auth_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_claude("sk-ant-x", "sys", "user")
    assert exc.value.status_code == 401
    # auth는 circuit breaker에 카운트 안 됨
    assert _circuit_breaker._failures == 0


async def test_call_claude_rate_limit_raises_429(monkeypatch):
    rl_exc = _make_anthropic_exc(anthropic.RateLimitError, message="rate", status_code=429)
    _patch_client(monkeypatch, side_effect=rl_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_claude("sk-ant-x", "sys", "user")
    assert exc.value.status_code == 429


async def test_call_claude_credit_error_returns_503(monkeypatch):
    """status=400 + 'credit' 메시지 → 502가 아닌 503 반환."""
    credit_exc = _make_anthropic_exc(
        anthropic.APIStatusError,
        message="account has insufficient CREDIT to continue",
        status_code=400,
    )
    _patch_client(monkeypatch, side_effect=credit_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_claude("sk-ant-x", "sys", "user")
    assert exc.value.status_code == 503


async def test_call_claude_status_error_records_failure_and_502(monkeypatch):
    api_exc = _make_anthropic_exc(
        anthropic.APIStatusError, message="server error", status_code=500,
    )
    _patch_client(monkeypatch, side_effect=api_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_claude("sk-ant-x", "sys", "user")
    assert exc.value.status_code == 502
    assert _circuit_breaker._failures == 1


async def test_call_claude_timeout_returns_504(monkeypatch):
    timeout_exc = _make_anthropic_exc(anthropic.APITimeoutError, message="timeout")
    _patch_client(monkeypatch, side_effect=timeout_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_claude("sk-ant-x", "sys", "user")
    assert exc.value.status_code == 504


async def test_call_claude_connection_error_returns_502(monkeypatch):
    conn_exc = _make_anthropic_exc(anthropic.APIConnectionError, message="conn refused")
    _patch_client(monkeypatch, side_effect=conn_exc)
    with pytest.raises(GenerationError) as exc:
        await _call_claude("sk-ant-x", "sys", "user")
    assert exc.value.status_code == 502


# ─────────────────────────────────────────
# generate_with_retry — 재시도 정책
# ─────────────────────────────────────────

async def test_generate_with_retry_no_retry_on_429(monkeypatch):
    """429는 재시도 없이 즉시 raise."""
    rl_exc = _make_anthropic_exc(anthropic.RateLimitError, message="rate", status_code=429)
    _patch_client(monkeypatch, side_effect=rl_exc)
    call_count = {"n": 0}

    async def _patched_call(*a, **kw):
        call_count["n"] += 1
        raise GenerationError("rate limit", status_code=429)

    monkeypatch.setattr("app.services.anthropic_client._call_claude", _patched_call)
    with pytest.raises(GenerationError) as exc:
        await generate_with_retry("sk-ant-x", "s", "u", max_retries=3)
    assert exc.value.status_code == 429
    assert call_count["n"] == 1  # 재시도 없음


async def test_generate_with_retry_invokes_on_attempt_callback(monkeypatch):
    monkeypatch.setattr(
        "app.services.anthropic_client._call_claude",
        AsyncMock(return_value='{"ok":true}'),
    )
    attempts = []
    result = await generate_with_retry(
        "sk-ant-x", "s", "u", on_attempt=lambda i: attempts.append(i),
    )
    assert result == {"ok": True}
    assert attempts == [0]


async def test_generate_with_retry_exhausts_and_raises_500(monkeypatch):
    err = GenerationError("transient", status_code=500)

    async def _failing_call(*a, **kw):
        raise err

    monkeypatch.setattr("app.services.anthropic_client._call_claude", _failing_call)
    monkeypatch.setattr("app.services.anthropic_client.asyncio.sleep", AsyncMock())
    with pytest.raises(GenerationError) as exc:
        await generate_with_retry("sk-ant-x", "s", "u", max_retries=2)
    assert exc.value.status_code == 500


# ─────────────────────────────────────────
# generate_study_content — 3단계 파이프라인 + progress callback
# ─────────────────────────────────────────

async def test_generate_study_content_runs_three_stages(monkeypatch):
    monkeypatch.setattr(
        "app.services.anthropic_client.generate_with_retry",
        AsyncMock(side_effect=[
            {"stage": "notes"},
            {"stage": "mcq"},
            {"stage": "fill"},
        ]),
    )
    progress = []
    notes, mcq, fill = await generate_study_content(
        "sk-ant-x",
        "sn", "pn", "sm", "pm", "sf", "pf",
        progress_callback=lambda pct, stage: progress.append((pct, stage)),
    )
    assert notes == {"stage": "notes"}
    assert mcq == {"stage": "mcq"}
    assert fill == {"stage": "fill"}
    # progress callback이 4번(10/50/80/100) 호출됨
    assert [p[0] for p in progress] == [10, 50, 80, 100]


async def test_generate_study_content_no_callback_does_not_raise(monkeypatch):
    monkeypatch.setattr(
        "app.services.anthropic_client.generate_with_retry",
        AsyncMock(return_value={"ok": True}),
    )
    # progress_callback 없이도 정상 동작
    notes, mcq, fill = await generate_study_content(
        "sk-ant-x", "sn", "pn", "sm", "pm", "sf", "pf",
    )
    assert notes == mcq == fill == {"ok": True}
