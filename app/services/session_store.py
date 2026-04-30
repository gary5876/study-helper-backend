"""In-memory + Redis session store for tracking generation state."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Literal, Optional

logger = logging.getLogger(__name__)

# Optional Redis dependency
try:
    import redis.asyncio as aioredis  # type: ignore
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False


@dataclass
class SessionRecord:
    session_id: str
    pdf_name: str
    page_count: int
    word_count: int
    s3_key: str  # key in S3 (or local path in dev)
    pdf_hash: str = ""  # SHA-256 of raw PDF bytes for question bank lookup
    user_id: Optional[str] = None  # owner (Supabase uid); None = guest
    status: Literal["uploaded", "processing", "complete", "failed"] = "uploaded"
    progress_pct: int = 0
    error_message: Optional[str] = None
    result_json: Optional[str] = None  # serialised StudyContent


# In-process fallback store (single-node dev / testing)
_local_store: dict[str, SessionRecord] = {}


class SessionStore:
    """
    Thin session store backed by Redis when available, otherwise in-process dict.
    TTL: 2 hours per session.
    """

    TTL = 7200  # 2 hours

    def __init__(self, redis_url: str | None = None):
        self._redis: "aioredis.Redis | None" = None
        self._redis_url = redis_url

    async def connect(self, tls_enabled: bool = False):
        if _REDIS_AVAILABLE and self._redis_url:
            try:
                kwargs: dict = {"decode_responses": True}
                if tls_enabled:
                    import ssl
                    kwargs["ssl"] = ssl.create_default_context()
                self._redis = aioredis.from_url(self._redis_url, **kwargs)
                await self._redis.ping()
                logger.info(
                    "SessionStore: connected to Redis at %s (TLS=%s)",
                    self._redis_url, tls_enabled,
                )
            except Exception as exc:
                logger.warning("SessionStore: Redis unavailable (%s), using in-memory fallback.", exc)
                self._redis = None

    async def save(self, record: SessionRecord) -> None:
        data = json.dumps(asdict(record))
        if self._redis:
            await self._redis.setex(f"session:{record.session_id}", self.TTL, data)
        else:
            _local_store[record.session_id] = record

    async def get(self, session_id: str) -> SessionRecord | None:
        if self._redis:
            raw = await self._redis.get(f"session:{session_id}")
            if raw is None:
                return None
            return SessionRecord(**json.loads(raw))
        return _local_store.get(session_id)

    async def update_status(
        self,
        session_id: str,
        status: str,
        progress_pct: int = 0,
        error_message: str | None = None,
        result_json: str | None = None,
    ) -> None:
        record = await self.get(session_id)
        if record is None:
            logger.warning("SessionStore: update_status called for unknown session %s", session_id)
            return
        record.status = status  # type: ignore[assignment]
        record.progress_pct = progress_pct
        if error_message is not None:
            record.error_message = error_message
        if result_json is not None:
            record.result_json = result_json
        await self.save(record)

    async def delete(self, session_id: str) -> bool:
        if self._redis:
            deleted = await self._redis.delete(f"session:{session_id}")
            return bool(deleted)
        if session_id in _local_store:
            del _local_store[session_id]
            return True
        return False


# Singleton instance, initialised at startup
_store: SessionStore | None = None


def get_store() -> SessionStore:
    if _store is None:
        raise RuntimeError("SessionStore not initialised. Call init_store() at startup.")
    return _store


async def init_store(redis_url: str | None = None, tls_enabled: bool = False) -> SessionStore:
    global _store
    _store = SessionStore(redis_url=redis_url)
    await _store.connect(tls_enabled=tls_enabled)
    return _store
