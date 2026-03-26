"""Google Gemini client with key-pool round-robin and rate-limit cooldown."""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from typing import Callable

import google.generativeai as genai

from app.core.config import get_settings
from app.core.exceptions import GenerationError
from app.services.json_utils import extract_json

logger = logging.getLogger(__name__)
settings = get_settings()

_COOLDOWN_SECONDS = 60

# Batch sizes chosen so each batch fits comfortably within 4096 / 2048 output tokens
MCQ_BATCH_SIZE = 5   # ~400 tokens/question × 5 ≈ 2000 tokens
FILL_BATCH_SIZE = 8  # ~200 tokens/question × 8 ≈ 1600 tokens


# ─────────────────────────────────────────
# Key pool
# ─────────────────────────────────────────

class GeminiKeyPool:
    """Thread-safe round-robin key pool with per-key cooldown on rate limit."""

    def __init__(self, keys: list[str]):
        if not keys:
            raise GenerationError(
                "Gemini API 키가 설정되지 않았습니다. 관리자에게 문의하세요.",
                status_code=503,
            )
        self._keys = keys
        self._index = 0
        self._cooldown_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def get_key(self) -> str:
        with self._lock:
            now = time.monotonic()
            for _ in range(len(self._keys)):
                key = self._keys[self._index % len(self._keys)]
                self._index += 1
                if self._cooldown_until.get(key, 0) <= now:
                    return key
            raise GenerationError(
                "모든 Gemini API 키가 일시적으로 사용 불가합니다. 잠시 후 다시 시도해주세요.",
                status_code=429,
            )

    def mark_rate_limited(self, key: str) -> None:
        with self._lock:
            self._cooldown_until[key] = time.monotonic() + _COOLDOWN_SECONDS
            logger.warning(
                "Gemini key ...%s rate-limited, cooling down for %ds", key[-6:], _COOLDOWN_SECONDS
            )


_key_pool: GeminiKeyPool | None = None
_pool_lock = threading.Lock()


def get_key_pool() -> GeminiKeyPool:
    global _key_pool
    with _pool_lock:
        if _key_pool is None:
            _key_pool = GeminiKeyPool(settings.gemini_keys_list)
        return _key_pool


# ─────────────────────────────────────────
# Low-level API call
# ─────────────────────────────────────────

class _RateLimitError(Exception):
    def __init__(self, key: str):
        self.key = key


async def _call_gemini(key: str, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
    """Single Gemini API call, returns raw text.

    Uses system_instruction + response_mime_type=application/json to maximise
    the chance of receiving a complete, valid JSON response.
    """
    genai.configure(api_key=key)
    model = genai.GenerativeModel(
        model_name=settings.GEMINI_MODEL,
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(
            max_output_tokens=max_tokens,
            response_mime_type="application/json",
        ),
    )
    try:
        response = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, lambda: model.generate_content(user_prompt)
            ),
            timeout=settings.GEMINI_TIMEOUT,
        )
        # Detect truncation before returning
        candidate = response.candidates[0] if response.candidates else None
        finish_reason = getattr(getattr(candidate, "finish_reason", None), "name", None)
        if finish_reason == "MAX_TOKENS":
            logger.warning("Gemini response truncated (MAX_TOKENS). max_tokens=%d", max_tokens)
            raise GenerationError(
                "서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
                status_code=500,
            )
        return response.text
    except GenerationError:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "quota" in msg or "rate" in msg or "429" in msg:
            raise _RateLimitError(key) from exc
        raise GenerationError(
            "서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
            status_code=502,
        ) from exc


# ─────────────────────────────────────────
# Single call with key-pool retry
# ─────────────────────────────────────────

async def _gemini_generate(system_prompt: str, user_prompt: str, max_tokens: int) -> dict:
    """Try available keys in round-robin; fall back on rate limit."""
    pool = get_key_pool()
    last_error: Exception | None = None
    for _ in range(len(settings.gemini_keys_list) + 1):
        try:
            key = pool.get_key()
        except GenerationError:
            break
        try:
            raw = await _call_gemini(key, system_prompt, user_prompt, max_tokens)
            return extract_json(raw)
        except _RateLimitError as exc:
            pool.mark_rate_limited(exc.key)
            last_error = exc
        except GenerationError as exc:
            last_error = exc
            break
    raise GenerationError(
        f"Gemini 콘텐츠 생성에 실패했습니다: {last_error}",
        status_code=502,
    )


# ─────────────────────────────────────────
# Batched call
# ─────────────────────────────────────────

async def _gemini_generate_batched(
    system_prompt: str,
    build_user_prompt: Callable[[int, list[dict]], str],
    total_count: int,
    batch_size: int,
    max_tokens: int,
) -> dict:
    """Generate questions in sequential batches and merge results.

    Args:
        system_prompt: System instruction (constant across batches).
        build_user_prompt: Callable(batch_count, already_generated) → user prompt string.
            Receives the number of questions needed for this batch and the list of
            question dicts already generated, so it can inject a dedup hint.
        total_count: Total number of questions desired.
        batch_size: Max questions per API call.
        max_tokens: Output token limit per call.
    """
    num_batches = math.ceil(total_count / batch_size)
    all_questions: list[dict] = []

    for batch_idx in range(num_batches):
        remaining = total_count - len(all_questions)
        current_size = min(batch_size, remaining)
        if current_size <= 0:
            break

        user_prompt = build_user_prompt(current_size, all_questions)
        result = await _gemini_generate(system_prompt, user_prompt, max_tokens)
        batch_questions = result.get("questions", [])
        all_questions.extend(batch_questions)
        logger.info(
            "Batch %d/%d: received %d questions (running total: %d)",
            batch_idx + 1, num_batches, len(batch_questions), len(all_questions),
        )

    return {"questions": all_questions}


# ─────────────────────────────────────────
# High-level client
# ─────────────────────────────────────────

class GeminiClient:
    """High-level async client for Gemini-based study content generation."""

    async def generate_notes(self, system_notes: str, prompt_notes: str) -> dict:
        """Generate study notes (single call — output fits within 4096 tokens)."""
        return await _gemini_generate(system_notes, prompt_notes, max_tokens=4096)

    async def generate_mcq_batched(
        self,
        full_text: str,
        notes_dict: dict,
        total_count: int,
    ) -> dict:
        """Generate MCQ questions in sequential batches of MCQ_BATCH_SIZE.

        Each batch receives a dedup hint listing previously generated question texts
        to minimise topical overlap across batches.
        """
        from app.services.prompt_builder import MCQ_SYSTEM, build_mcq_prompt

        def build_prompt(count: int, existing: list[dict]) -> str:
            _, user_prompt = build_mcq_prompt(full_text, notes_dict, count)
            if existing:
                existing_qs = "\n".join(
                    f"- {q['question']}"
                    for q in existing
                    if isinstance(q, dict) and q.get("question")
                )
                user_prompt += (
                    "\n\nIMPORTANT: Generate questions on DIFFERENT topics/concepts "
                    f"from these already-generated questions:\n{existing_qs}"
                )
            return user_prompt

        return await _gemini_generate_batched(
            MCQ_SYSTEM, build_prompt, total_count, MCQ_BATCH_SIZE, max_tokens=4096
        )

    async def generate_fill_batched(
        self,
        full_text: str,
        notes_dict: dict,
        total_count: int,
    ) -> dict:
        """Generate fill-in-blank questions in sequential batches of FILL_BATCH_SIZE.

        Each batch receives a dedup hint listing previously used answers.
        """
        from app.services.prompt_builder import FILL_SYSTEM, build_fill_prompt

        def build_prompt(count: int, existing: list[dict]) -> str:
            _, user_prompt = build_fill_prompt(full_text, notes_dict, count)
            if existing:
                existing_answers = "\n".join(
                    f"- {q['answer']}"
                    for q in existing
                    if isinstance(q, dict) and q.get("answer")
                )
                user_prompt += (
                    "\n\nIMPORTANT: Generate blanks for DIFFERENT terms/concepts "
                    f"from these already-used answers:\n{existing_answers}"
                )
            return user_prompt

        return await _gemini_generate_batched(
            FILL_SYSTEM, build_prompt, total_count, FILL_BATCH_SIZE, max_tokens=2048
        )
