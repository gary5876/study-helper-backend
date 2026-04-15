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
) -> None:
    """Persist generated content. Silently skips if DB is unavailable."""
    if not _pool or not pdf_hash:
        return
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
    except Exception as exc:
        logger.warning("QuestionBank.save_to_bank failed: %s", exc)
