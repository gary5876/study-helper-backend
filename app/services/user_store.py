"""Supabase PostgreSQL data access layer for user-scoped data."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_pool: Optional[asyncpg.Pool] = None


async def init_user_store(database_url: str) -> None:
    global _pool
    try:
        _pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
        logger.info("UserStore: connected to Supabase PostgreSQL.")
    except Exception as exc:
        logger.warning("UserStore: Supabase PostgreSQL unavailable (%s).", exc)
        _pool = None


async def close_user_store() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_user_store() -> "UserStore":
    return UserStore(_pool)


class UserStore:
    def __init__(self, pool: Optional[asyncpg.Pool]):
        self._pool = pool

    def _require_pool(self) -> asyncpg.Pool:
        if not self._pool:
            from fastapi import HTTPException
            raise HTTPException(status_code=503, detail="데이터베이스에 연결할 수 없습니다")
        return self._pool

    # ── Subjects ──────────────────────────────────────

    async def get_subjects(self, user_id: str) -> list[dict]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id::text, name, color, created_at FROM user_subjects "
                "WHERE user_id = $1::uuid ORDER BY created_at",
                user_id,
            )
        return [dict(r) for r in rows]

    async def create_subject(self, user_id: str, name: str, color: str) -> dict:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO user_subjects (user_id, name, color) VALUES ($1::uuid, $2, $3) "
                "ON CONFLICT (user_id, name) DO UPDATE SET color = EXCLUDED.color "
                "RETURNING id::text, name, color, created_at",
                user_id, name, color,
            )
        return dict(row)

    async def delete_subject(self, user_id: str, subject_id: str) -> bool:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM user_subjects WHERE id = $1::uuid AND user_id = $2::uuid",
                subject_id, user_id,
            )
        return result.split()[-1] != "0"

    async def sync_subjects(self, user_id: str, subjects: list) -> int:
        if not subjects:
            return 0
        pool = self._require_pool()
        count = 0
        async with pool.acquire() as conn:
            for s in subjects:
                await conn.execute(
                    "INSERT INTO user_subjects (user_id, name, color) VALUES ($1::uuid, $2, $3) "
                    "ON CONFLICT (user_id, name) DO NOTHING",
                    user_id, s.name, s.color,
                )
                count += 1
        return count

    # ── Sessions ──────────────────────────────────────

    async def get_sessions(self, user_id: str) -> list[dict]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id::text, pdf_name, pdf_hash, subject_id::text, "
                "page_count, word_count, status, created_at, last_accessed "
                "FROM user_sessions WHERE user_id = $1::uuid ORDER BY created_at DESC",
                user_id,
            )

        # Self-heal (크로스 DB 대응): pending 세션 중 question_bank 에 이미 결과가
        # 캐시된 것(= 과거 생성이 성공했으나 sync 실패/다운그레이드로 pending에 갇힌
        # 행)을 ready 로 승격. question_bank 와 user_sessions 이 서로 다른 DB 풀일
        # 수 있으므로 각각 조회한 뒤 매치된 id 만 별도 UPDATE.
        pending_ids: list[str] = []
        pending_hashes: list[str] = []
        for r in rows:
            if r["status"] == "pending" and r["pdf_hash"]:
                pending_ids.append(r["id"])
                pending_hashes.append(r["pdf_hash"])

        if pending_hashes:
            try:
                from app.services.question_bank import _pool as qb_pool  # type: ignore
                ready_hashes: set[str] = set()
                if qb_pool is not None:
                    async with qb_pool.acquire() as qb_conn:
                        qb_rows = await qb_conn.fetch(
                            "SELECT pdf_hash FROM question_bank WHERE pdf_hash = ANY($1::text[])",
                            pending_hashes,
                        )
                        ready_hashes = {r["pdf_hash"] for r in qb_rows}
                if ready_hashes:
                    heal_ids = [
                        rid for rid, h in zip(pending_ids, pending_hashes)
                        if h in ready_hashes
                    ]
                    if heal_ids:
                        async with pool.acquire() as conn2:
                            await conn2.execute(
                                "UPDATE user_sessions SET status = 'ready' "
                                "WHERE user_id = $1::uuid AND id = ANY($2::uuid[])",
                                user_id, heal_ids,
                            )
                        logger.info(
                            "user_sessions self-heal: user=%s healed=%d",
                            user_id, len(heal_ids),
                        )
                        # 반환 rows 에도 반영
                        heal_set = set(heal_ids)
                        rows = [
                            dict(r, status="ready") if r["id"] in heal_set else dict(r)
                            for r in rows
                        ]
                        return [r if isinstance(r, dict) else dict(r) for r in rows]
            except Exception as exc:
                logger.warning("user_sessions self-heal skipped: %s", exc)

        return [dict(r) for r in rows]

    async def create_session(self, user_id: str, body) -> dict:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            subject_id = body.subject_id  # may be None
            # Verify subject ownership if provided
            if subject_id:
                owner_check = await conn.fetchval(
                    "SELECT 1 FROM user_subjects WHERE id = $1::uuid AND user_id = $2::uuid",
                    subject_id, user_id,
                )
                if not owner_check:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=403, detail="해당 과목에 대한 접근 권한이 없습니다")
            explicit_id = getattr(body, "id", None)
            if explicit_id:
                row = await conn.fetchrow(
                    "INSERT INTO user_sessions "
                    "(id, user_id, pdf_name, pdf_hash, subject_id, page_count, word_count, status) "
                    "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6, $7, $8) "
                    "RETURNING id::text, pdf_name, pdf_hash, subject_id::text, "
                    "page_count, word_count, status, created_at, last_accessed",
                    explicit_id, user_id, body.pdf_name, body.pdf_hash,
                    subject_id, body.page_count, body.word_count, body.status,
                )
            else:
                row = await conn.fetchrow(
                    "INSERT INTO user_sessions "
                    "(user_id, pdf_name, pdf_hash, subject_id, page_count, word_count, status) "
                    "VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7) "
                    "RETURNING id::text, pdf_name, pdf_hash, subject_id::text, "
                    "page_count, word_count, status, created_at, last_accessed",
                    user_id, body.pdf_name, body.pdf_hash,
                    subject_id, body.page_count, body.word_count, body.status,
                )
        return dict(row)

    async def upsert_session(self, user_id: str, body) -> dict:
        """user_sessions에 INSERT하거나, (user_id, pdf_hash) 충돌 시 기존 행을
        재사용하고 메타데이터/상태를 갱신. 반환되는 id가 권위 있는 session_id.
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            subject_id = body.subject_id  # may be None
            if subject_id:
                owner_check = await conn.fetchval(
                    "SELECT 1 FROM user_subjects WHERE id = $1::uuid AND user_id = $2::uuid",
                    subject_id, user_id,
                )
                if not owner_check:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=403, detail="해당 과목에 대한 접근 권한이 없습니다")

            explicit_id = getattr(body, "id", None)
            # pdf_hash가 없으면 upsert가 동작하지 않으므로 일반 insert 경로로 폴백.
            if not body.pdf_hash:
                return await self.create_session(user_id, body)

            # 재업로드 시 이미 ready/failed 였던 행이 pending 으로 다운그레이드되는 것을 방지.
            # status CASE: 기존 status가 ready/failed면 그대로 유지, 아니면 EXCLUDED 적용.
            status_clause = (
                "status = CASE "
                "  WHEN user_sessions.status IN ('ready','failed') THEN user_sessions.status "
                "  ELSE EXCLUDED.status "
                "END"
            )
            if explicit_id:
                row = await conn.fetchrow(
                    "INSERT INTO user_sessions "
                    "(id, user_id, pdf_name, pdf_hash, subject_id, page_count, word_count, status) "
                    "VALUES ($1::uuid, $2::uuid, $3, $4, $5::uuid, $6, $7, $8) "
                    "ON CONFLICT (user_id, pdf_hash) DO UPDATE SET "
                    "  pdf_name = EXCLUDED.pdf_name, "
                    "  subject_id = EXCLUDED.subject_id, "
                    "  page_count = EXCLUDED.page_count, "
                    "  word_count = EXCLUDED.word_count, "
                    f"  {status_clause}, "
                    "  last_accessed = now() "
                    "RETURNING id::text, pdf_name, pdf_hash, subject_id::text, "
                    "page_count, word_count, status, created_at, last_accessed",
                    explicit_id, user_id, body.pdf_name, body.pdf_hash,
                    subject_id, body.page_count, body.word_count, body.status,
                )
            else:
                row = await conn.fetchrow(
                    "INSERT INTO user_sessions "
                    "(user_id, pdf_name, pdf_hash, subject_id, page_count, word_count, status) "
                    "VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7) "
                    "ON CONFLICT (user_id, pdf_hash) DO UPDATE SET "
                    "  pdf_name = EXCLUDED.pdf_name, "
                    "  subject_id = EXCLUDED.subject_id, "
                    "  page_count = EXCLUDED.page_count, "
                    "  word_count = EXCLUDED.word_count, "
                    f"  {status_clause}, "
                    "  last_accessed = now() "
                    "RETURNING id::text, pdf_name, pdf_hash, subject_id::text, "
                    "page_count, word_count, status, created_at, last_accessed",
                    user_id, body.pdf_name, body.pdf_hash,
                    subject_id, body.page_count, body.word_count, body.status,
                )
        return dict(row)

    async def update_session_status(self, user_id: str, session_id: str, status: str) -> bool:
        """user_sessions 행의 status를 갱신. 성공 시 True."""
        if status not in ("pending", "ready", "failed"):
            raise ValueError(f"invalid status: {status}")
        pool = self._require_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "UPDATE user_sessions SET status = $1 "
                "WHERE id = $2::uuid AND user_id = $3::uuid",
                status, session_id, user_id,
            )
        # asyncpg execute returns 'UPDATE <n>'
        return result.endswith(" 1")

    async def delete_session(self, user_id: str, session_id: str) -> bool:
        """user_sessions 행을 삭제. ON DELETE CASCADE로 user_review_schedule·
        user_attempts도 함께 제거됨. 성공 시 True.
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM user_sessions "
                "WHERE id = $1::uuid AND user_id = $2::uuid",
                session_id, user_id,
            )
        return result.endswith(" 1")

    async def sync_sessions(self, user_id: str, sessions: list) -> int:
        if not sessions:
            return 0
        pool = self._require_pool()
        count = 0
        async with pool.acquire() as conn:
            # Pre-validate subject ownership for all referenced subject_ids
            subject_ids = {s.subject_id for s in sessions if s.subject_id}
            if subject_ids:
                owned = await conn.fetch(
                    "SELECT id::text FROM user_subjects WHERE user_id = $1::uuid AND id = ANY($2::uuid[])",
                    user_id, list(subject_ids),
                )
                owned_ids = {r["id"] for r in owned}
                invalid = subject_ids - owned_ids
                if invalid:
                    from fastapi import HTTPException
                    raise HTTPException(status_code=403, detail="해당 과목에 대한 접근 권한이 없습니다")
            for s in sessions:
                await conn.execute(
                    "INSERT INTO user_sessions "
                    "(user_id, pdf_name, pdf_hash, subject_id, page_count, word_count, status) "
                    "VALUES ($1::uuid, $2, $3, $4::uuid, $5, $6, $7) "
                    "ON CONFLICT (user_id, pdf_hash) DO NOTHING",
                    user_id, s.pdf_name, s.pdf_hash,
                    s.subject_id, s.page_count, s.word_count, s.status,
                )
                count += 1
        return count

    # ── Review Schedule ───────────────────────────────

    async def get_due_reviews(self, user_id: str) -> list[dict]:
        pool = self._require_pool()
        now = datetime.now(timezone.utc)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id::text, session_id::text, question_id, question_type, "
                "interval_days, next_review_at, ease_factor, repetitions, status "
                "FROM user_review_schedule "
                "WHERE user_id = $1::uuid AND next_review_at <= $2 AND status != 'done' "
                "ORDER BY next_review_at",
                user_id, now,
            )
        return [dict(r) for r in rows]

    async def upsert_review(self, user_id: str, body) -> dict:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO user_review_schedule "
                "(user_id, session_id, question_id, question_type, "
                "interval_days, next_review_at, ease_factor, repetitions, status) "
                "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9) "
                "ON CONFLICT (user_id, session_id, question_id) DO UPDATE SET "
                "interval_days = EXCLUDED.interval_days, "
                "next_review_at = EXCLUDED.next_review_at, "
                "ease_factor = EXCLUDED.ease_factor, "
                "repetitions = EXCLUDED.repetitions, "
                "status = EXCLUDED.status "
                "RETURNING id::text, session_id::text, question_id, question_type, "
                "interval_days, next_review_at, ease_factor, repetitions, status",
                user_id, body.session_id, body.question_id, body.question_type,
                body.interval_days, body.next_review_at,
                body.ease_factor, body.repetitions, body.status,
            )
        return dict(row)

    async def sync_reviews(self, user_id: str, reviews: list) -> int:
        if not reviews:
            return 0
        pool = self._require_pool()
        count = 0
        async with pool.acquire() as conn:
            for r in reviews:
                await conn.execute(
                    "INSERT INTO user_review_schedule "
                    "(user_id, session_id, question_id, question_type, "
                    "interval_days, next_review_at, ease_factor, repetitions, status) "
                    "VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9) "
                    "ON CONFLICT (user_id, session_id, question_id) DO NOTHING",
                    user_id, r.session_id, r.question_id, r.question_type,
                    r.interval_days, r.next_review_at,
                    r.ease_factor, r.repetitions, r.status,
                )
                count += 1
        return count
