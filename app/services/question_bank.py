"""PostgreSQL-backed question bank keyed by PDF content hash (SHA-256)."""
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS question_bank (
    pdf_hash    TEXT PRIMARY KEY,
    pdf_name    TEXT NOT NULL,
    page_count  INTEGER NOT NULL,
    word_count  INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count   INTEGER NOT NULL DEFAULT 0
);
"""

_pool: Optional[asyncpg.Pool] = None


async def init_question_bank(database_url: str) -> None:
    global _pool
    try:
        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        async with _pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE)
        logger.info("QuestionBank: connected to PostgreSQL and table ready.")
    except Exception as exc:
        logger.warning("QuestionBank: PostgreSQL unavailable (%s). Question bank disabled.", exc)
        _pool = None


async def close_question_bank() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def get_cached(pdf_hash: str) -> Optional[str]:
    """Return cached content_json for the given hash, or None if not found."""
    if not _pool or not pdf_hash:
        return None
    try:
        async with _pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT content_json FROM question_bank WHERE pdf_hash = $1",
                pdf_hash,
            )
            if row is None:
                return None
            await conn.execute(
                "UPDATE question_bank SET hit_count = hit_count + 1 WHERE pdf_hash = $1",
                pdf_hash,
            )
            return row["content_json"]
    except Exception as exc:
        logger.warning("QuestionBank.get_cached failed: %s", exc)
        return None


async def save_to_bank(
    pdf_hash: str,
    pdf_name: str,
    page_count: int,
    word_count: int,
    content_json: str,
) -> bool:
    """Persist generated content as shared cache. Returns True on success.

    이 함수는 **best-effort**다. 실패해도 호출자는 진행을 중단하면 안 된다
    (primary 저장소는 user_sessions.result_json이고 이미 커밋된 상태여야 함).
    다만 실패는 logger.error로 명확히 남겨 관측 가능하게 한다 — 과거처럼 warning
    으로 삼키지 않는다.
    """
    if not _pool:
        logger.error("QuestionBank: save skipped — pool not initialized (pdf_hash=%s)", pdf_hash[:12] if pdf_hash else "-")
        return False
    if not pdf_hash:
        logger.error("QuestionBank: save skipped — empty pdf_hash")
        return False
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO question_bank (pdf_hash, pdf_name, page_count, word_count, content_json)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (pdf_hash) DO NOTHING
                """,
                pdf_hash, pdf_name, page_count, word_count, content_json,
            )
        logger.info("QuestionBank: saved pdf_hash=%s (%s)", pdf_hash[:12], pdf_name)
        return True
    except Exception as exc:
        logger.error("QuestionBank.save_to_bank failed (pdf_hash=%s): %s", pdf_hash[:12], exc)
        return False
