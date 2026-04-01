"""Wrapper around the OpenAI Chat Completions API with retry and circuit-breaker."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable

import openai

from app.core.config import get_settings
from app.core.exceptions import GenerationError
from app.services.json_utils import extract_json

logger = logging.getLogger(__name__)
settings = get_settings()


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
                    "CircuitBreaker OPEN: OpenAI API has failed %d times. "
                    "Blocking requests for %ds.",
                    self._failures, self.RECOVERY_TIMEOUT,
                )


_circuit_breaker = _CircuitBreaker()


async def _call_gpt(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    model: str | None = None,
) -> str:
    """Make a single OpenAI Chat Completions call and return the text response."""
    if _circuit_breaker.is_open:
        raise GenerationError(
            "OpenAI API is temporarily unavailable. Please try again later.",
            status_code=503,
        )

    client = openai.AsyncOpenAI(
        api_key=api_key,
        timeout=settings.OPENAI_TIMEOUT,
    )
    try:
        response = await client.chat.completions.create(
            model=model or settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        _circuit_breaker.record_success()
        return response.choices[0].message.content or ""
    except openai.AuthenticationError as exc:
        raise GenerationError(
            "Invalid OpenAI API key. Please check your key and try again.",
            status_code=401,
        ) from exc
    except openai.RateLimitError as exc:
        raise GenerationError(
            "OpenAI API rate limit reached. Please wait a moment and try again.",
            status_code=429,
        ) from exc
    except openai.APIStatusError as exc:
        if exc.status_code in (402, 403):
            raise GenerationError(
                "OpenAI API quota exceeded or billing issue. Please check your account.",
                status_code=503,
            ) from exc
        _circuit_breaker.record_failure()
        raise GenerationError(
            "서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
            status_code=502,
        ) from exc
    except openai.APITimeoutError as exc:
        _circuit_breaker.record_failure()
        raise GenerationError(
            f"OpenAI API timed out after {settings.OPENAI_TIMEOUT}s. Please try again.",
            status_code=504,
        ) from exc
    except openai.APIConnectionError as exc:
        _circuit_breaker.record_failure()
        raise GenerationError(
            "Could not connect to OpenAI API. Please check your network.",
            status_code=502,
        ) from exc


async def generate_with_retry(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    max_retries: int | None = None,
    on_attempt: Callable[[int], None] | None = None,
    model: str | None = None,
) -> dict:
    """Call GPT, parse JSON response, retry on failure."""
    retries = max_retries if max_retries is not None else settings.MAX_RETRIES

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if on_attempt:
            on_attempt(attempt)
        try:
            raw = await _call_gpt(api_key, system_prompt, user_prompt, max_tokens, model)
            return extract_json(raw)
        except GenerationError as exc:
            if exc.status_code in (401, 429, 503):
                raise
            last_error = exc
            if attempt < retries:
                wait = 2 ** attempt
                logger.warning("GPT attempt %d failed, retrying in %ds: %s", attempt + 1, wait, exc)
                await asyncio.sleep(wait)

    raise GenerationError(
        f"Content generation failed after {retries + 1} attempts: {last_error}",
        status_code=500,
    )
