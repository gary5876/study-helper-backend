"""Wrapper around the TimelyGPT API with token caching, retry, and circuit-breaker.

Authentication flow (mirrored from @timely/gpt-sdk):
  1. GET /sdk-auth/authenticate  (header: X-Timely-API: {api_key})
     → {"success": true, "data": {"access_token": "..."}}  (valid 55 min)
  2. POST /llm-completion  (header: Authorization: Bearer {access_token})
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Callable

import httpx

from app.core.config import get_settings
from app.core.exceptions import GenerationError
from app.services.json_utils import extract_json

logger = logging.getLogger(__name__)
settings = get_settings()

_BASE_URL = "https://hello.timelygpt.co.kr/api/v2/chat"

# ─────────────────────────────────────────
# Access-token cache  (key → (token, expires_monotonic))
# ─────────────────────────────────────────

_token_cache: dict[str, tuple[str, float]] = {}
_token_lock = asyncio.Lock()


async def _get_access_token(api_key: str) -> str:
    """Exchange API key for a Bearer token, cached for 55 minutes."""
    async with _token_lock:
        cached = _token_cache.get(api_key)
        if cached:
            token, expires_at = cached
            if time.monotonic() < expires_at:
                return token

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{_BASE_URL}/sdk-auth/authenticate",
                    headers={"X-Timely-API": api_key},
                    timeout=10.0,
                )
        except httpx.TimeoutException as exc:
            raise GenerationError("TimelyGPT authentication timed out.", status_code=504) from exc
        except httpx.RequestError as exc:
            raise GenerationError(f"Could not connect to TimelyGPT: {exc}", status_code=502) from exc

        if resp.status_code == 401:
            raise GenerationError(
                "Invalid TimelyGPT API key. Please check your key and try again.",
                status_code=401,
            )
        if not resp.is_success:
            raise GenerationError(
                f"TimelyGPT authentication failed (HTTP {resp.status_code}).",
                status_code=502,
            )

        data = resp.json()
        access_token: str | None = data.get("data", {}).get("access_token")
        if not access_token:
            raise GenerationError("TimelyGPT returned no access token.", status_code=502)

        _token_cache[api_key] = (access_token, time.monotonic() + 55 * 60)
        return access_token


# ─────────────────────────────────────────
# Circuit breaker (in-process, per worker)
# ─────────────────────────────────────────

class _CircuitBreaker:
    FAILURE_THRESHOLD = 5
    RECOVERY_TIMEOUT = 60

    def __init__(self):
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return False
            if time.monotonic() - self._opened_at >= self.RECOVERY_TIMEOUT:
                self._opened_at = None
                return False
            return True

    def record_success(self):
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self):
        with self._lock:
            self._failures += 1
            if self._failures >= self.FAILURE_THRESHOLD:
                self._opened_at = time.monotonic()
                logger.error(
                    "CircuitBreaker OPEN: TimelyGPT API has failed %d times. "
                    "Blocking requests for %ds.",
                    self._failures, self.RECOVERY_TIMEOUT,
                )


_circuit_breaker = _CircuitBreaker()


async def _call_timely(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    session_id: str | None = None,
) -> str:
    """Make a single TimelyGPT completion call and return the message text."""
    if _circuit_breaker.is_open:
        raise GenerationError(
            "TimelyGPT API is temporarily unavailable. Please try again later.",
            status_code=503,
        )

    access_token = await _get_access_token(api_key)

    payload = {
        "session_id": session_id or str(uuid.uuid4()),
        "messages": [{"role": "user", "content": user_prompt}],
        "model": settings.TIMELY_MODEL,
        "instructions": system_prompt,
        "stream": False,
        "locale": "ko",
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_BASE_URL}/llm-completion",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=settings.TIMELY_TIMEOUT,
            )
    except httpx.TimeoutException as exc:
        _circuit_breaker.record_failure()
        raise GenerationError(
            f"TimelyGPT API timed out after {settings.TIMELY_TIMEOUT}s. Please try again.",
            status_code=504,
        ) from exc
    except httpx.RequestError as exc:
        _circuit_breaker.record_failure()
        raise GenerationError(
            "Could not connect to TimelyGPT API. Please check your network.",
            status_code=502,
        ) from exc

    if resp.status_code == 401:
        # Token may have expired; evict cache so next retry re-authenticates
        _token_cache.pop(api_key, None)
        raise GenerationError(
            "TimelyGPT authentication failed. Please verify your API key.",
            status_code=401,
        )
    if resp.status_code == 429:
        raise GenerationError(
            "TimelyGPT API rate limit reached. Please wait and try again.",
            status_code=429,
        )
    if resp.status_code in (402, 403):
        raise GenerationError(
            "TimelyGPT API quota exceeded or billing issue. Please check your account.",
            status_code=503,
        )
    if not resp.is_success:
        _circuit_breaker.record_failure()
        raise GenerationError(
            "서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
            status_code=502,
        )

    _circuit_breaker.record_success()
    data = resp.json()

    if data.get("type") == "final_response":
        return data.get("message", "")

    raise GenerationError(
        f"Unexpected TimelyGPT response type: {data.get('type')}",
        status_code=502,
    )


async def generate_with_retry(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    max_retries: int | None = None,
    on_attempt: Callable[[int], None] | None = None,
    session_id: str | None = None,
) -> dict:
    """Call TimelyGPT, parse JSON response, retry on failure."""
    retries = max_retries if max_retries is not None else settings.MAX_RETRIES

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if on_attempt:
            on_attempt(attempt)
        try:
            raw = await _call_timely(api_key, system_prompt, user_prompt, max_tokens, session_id)
            return extract_json(raw)
        except GenerationError as exc:
            if exc.status_code in (401, 429, 503):
                raise
            last_error = exc
            if attempt < retries:
                wait = 2 ** attempt
                logger.warning(
                    "TimelyGPT attempt %d failed, retrying in %ds: %s",
                    attempt + 1, wait, exc,
                )
                await asyncio.sleep(wait)

    raise GenerationError(
        f"Content generation failed after {retries + 1} attempts: {last_error}",
        status_code=500,
    )
