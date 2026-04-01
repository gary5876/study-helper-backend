"""Wrapper around the Anthropic API with retry, timeout, and circuit-breaker."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import anthropic
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    RetryError,
)

from app.core.config import get_settings
from app.core.exceptions import GenerationError
from app.services.json_utils import extract_json

logger = logging.getLogger(__name__)
settings = get_settings()

# ─────────────────────────────────────────
# Circuit breaker state (in-process, per worker)
# For multi-worker deployments use Redis-backed state instead.
# ─────────────────────────────────────────
import threading
import time

class _CircuitBreaker:
    """Simple half-open circuit breaker for the Anthropic API."""

    FAILURE_THRESHOLD = 5     # consecutive failures before opening
    RECOVERY_TIMEOUT = 60     # seconds before attempting half-open

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
                # Allow one probe request (half-open)
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
                    "CircuitBreaker OPEN: Anthropic API has failed %d times. "
                    "Blocking requests for %ds.",
                    self._failures, self.RECOVERY_TIMEOUT,
                )


_circuit_breaker = _CircuitBreaker()


async def _call_claude(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    model: str | None = None,
) -> str:
    """Make a single API call and return the text response."""
    if _circuit_breaker.is_open:
        raise GenerationError(
            "Anthropic API is temporarily unavailable. Please try again later.",
            status_code=503,
        )

    client = anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=settings.ANTHROPIC_TIMEOUT,
    )
    try:
        message = await client.messages.create(
            model=model or settings.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        _circuit_breaker.record_success()
        return message.content[0].text
    except anthropic.AuthenticationError as exc:
        # Auth failures do not count toward the circuit breaker
        raise GenerationError(
            "Invalid Anthropic API key. Please check your key and try again.",
            status_code=401,
        ) from exc
    except anthropic.RateLimitError as exc:
        raise GenerationError(
            "Anthropic API rate limit reached. Please wait a moment and try again.",
            status_code=429,
        ) from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code == 400 and "credit" in str(exc.message).lower():
            raise GenerationError(
                "서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
                status_code=503,
            ) from exc
        _circuit_breaker.record_failure()
        raise GenerationError(
            "서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
            status_code=502,
        ) from exc
    except anthropic.APITimeoutError as exc:
        _circuit_breaker.record_failure()
        raise GenerationError(
            f"Anthropic API timed out after {settings.ANTHROPIC_TIMEOUT}s. Please try again.",
            status_code=504,
        ) from exc
    except anthropic.APIConnectionError as exc:
        _circuit_breaker.record_failure()
        raise GenerationError(
            "Could not connect to Anthropic API. Please check your network.",
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
    """
    Call Claude, parse JSON response, retry on failure.

    Returns a parsed dict. Raises GenerationError after exhausting retries.
    """
    retries = max_retries if max_retries is not None else settings.MAX_RETRIES

    last_error: Exception | None = None
    for attempt in range(retries + 1):
        if on_attempt:
            on_attempt(attempt)
        try:
            raw = await _call_claude(api_key, system_prompt, user_prompt, max_tokens, model)
            return extract_json(raw)
        except GenerationError as exc:
            # Auth / rate limit / service errors should not be retried
            if exc.status_code in (401, 429, 503):
                raise
            last_error = exc
            if attempt < retries:
                wait = 2 ** attempt  # exponential back-off: 1s, 2s
                logger.warning("Generation attempt %d failed, retrying in %ds: %s", attempt + 1, wait, exc)
                await asyncio.sleep(wait)

    raise GenerationError(
        f"Content generation failed after {retries + 1} attempts: {last_error}",
        status_code=500,
    )


async def generate_study_content(
    api_key: str,
    system_notes: str,
    prompt_notes: str,
    system_mcq: str,
    prompt_mcq: str,
    system_fill: str,
    prompt_fill: str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> tuple[dict, dict, dict]:
    """
    Run the three-stage generation pipeline sequentially.

    Returns (notes_dict, mcq_dict, fill_dict).
    Progress callback receives (percent: int, stage: str).
    """
    def _cb(pct: int, stage: str):
        if progress_callback:
            progress_callback(pct, stage)

    _cb(10, "Generating study notes…")
    notes = await generate_with_retry(api_key, system_notes, prompt_notes, max_tokens=4096)

    _cb(50, "Generating multiple-choice questions…")
    mcq = await generate_with_retry(api_key, system_mcq, prompt_mcq, max_tokens=4096)

    _cb(80, "Generating fill-in-the-blank questions…")
    fill = await generate_with_retry(api_key, system_fill, prompt_fill, max_tokens=2048)

    _cb(100, "Done")
    return notes, mcq, fill
