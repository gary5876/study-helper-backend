"""User data API — sessions, subjects, review schedule, sync."""
from __future__ import annotations

import logging
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import require_current_user
from app.services.user_store import UserStore, get_user_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/user", tags=["user"])


# ─────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────

class SubjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    color: str = Field(default="#6c63ff", pattern=r'^#[0-9a-fA-F]{6}$')


class SubjectResponse(BaseModel):
    id: str
    name: str
    color: str
    created_at: datetime


class SessionCreate(BaseModel):
    id: Optional[str] = Field(default=None, max_length=36, pattern=r'^[0-9a-f-]{36}$')
    pdf_name: str = Field(min_length=1, max_length=255)
    pdf_hash: Optional[str] = Field(default=None, max_length=64, pattern=r'^[a-f0-9]{64}$')
    subject_id: Optional[str] = Field(default=None, max_length=36)
    page_count: int = Field(default=0, ge=0, le=10000)
    word_count: int = Field(default=0, ge=0, le=10_000_000)
    status: str = Field(default="pending", pattern=r'^(pending|ready|failed)$')


class SessionResponse(BaseModel):
    id: str
    pdf_name: str
    pdf_hash: Optional[str]
    subject_id: Optional[str]
    page_count: int
    word_count: int
    status: str
    created_at: datetime
    last_accessed: Optional[datetime]


class ReviewScheduleUpsert(BaseModel):
    session_id: str = Field(max_length=36)
    question_id: str = Field(max_length=36)
    question_type: str = Field(pattern=r'^(mcq|fill)$')
    interval_days: int = Field(default=1, ge=1, le=365)
    next_review_at: datetime
    ease_factor: float = Field(default=2.5, ge=1.0, le=5.0)
    repetitions: int = Field(default=0, ge=0, le=100)
    status: str = Field(default="pending", pattern=r'^(pending|mastered|done)$')


class ReviewScheduleResponse(ReviewScheduleUpsert):
    id: str


class SyncPayload(BaseModel):
    subjects: list[SubjectCreate] = []
    sessions: list[SessionCreate] = []
    review_schedule: list[ReviewScheduleUpsert] = []


class SyncResponse(BaseModel):
    subjects_synced: int
    sessions_synced: int
    reviews_synced: int


# ─────────────────────────────────────────
# Subjects
# ─────────────────────────────────────────

@router.get("/subjects", response_model=list[SubjectResponse])
async def list_subjects(
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    return await store.get_subjects(user["user_id"])


@router.post("/subjects", response_model=SubjectResponse, status_code=201)
async def create_subject(
    body: SubjectCreate,
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    return await store.create_subject(user["user_id"], body.name, body.color)


@router.delete("/subjects/{subject_id}", status_code=204)
async def delete_subject(
    subject_id: str,
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    deleted = await store.delete_subject(user["user_id"], subject_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="과목을 찾을 수 없습니다")


# ─────────────────────────────────────────
# Sessions
# ─────────────────────────────────────────

@router.get("/sessions", response_model=list[SessionResponse])
async def list_sessions(
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    return await store.get_sessions(user["user_id"])


@router.post("/sessions", response_model=SessionResponse, status_code=201)
async def create_session(
    body: SessionCreate,
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    return await store.create_session(user["user_id"], body)


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    """user_sessions 행과 관련 복습 일정을 삭제하고 메모리 store도 정리."""
    deleted = await store.delete_session(user["user_id"], session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="세션을 찾을 수 없습니다")
    # 메모리 store도 함께 정리 (실패해도 DB 삭제는 이미 성공한 상태라 무시)
    try:
        from app.services.session_store import get_store as get_session_store
        await get_session_store().delete(session_id)
    except Exception:
        pass


# ─────────────────────────────────────────
# Review Schedule
# ─────────────────────────────────────────

@router.get("/review-schedule", response_model=list[ReviewScheduleResponse])
async def get_review_schedule(
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    """오늘 복습 예정 항목 반환."""
    return await store.get_due_reviews(user["user_id"])


@router.post("/review-schedule", response_model=ReviewScheduleResponse, status_code=201)
async def upsert_review(
    body: ReviewScheduleUpsert,
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    return await store.upsert_review(user["user_id"], body)


# ─────────────────────────────────────────
# Sync (로컬 → 클라우드 일괄 업로드)
# ─────────────────────────────────────────

@router.post("/sync", response_model=SyncResponse)
async def sync(
    body: SyncPayload,
    user: dict = Depends(require_current_user),
    store: UserStore = Depends(get_user_store),
):
    """
    모바일 최초 로그인 시 로컬 데이터를 클라우드에 일괄 업로드.
    멱등 보장 — 같은 데이터를 여러 번 보내도 중복 생성 안 됨.
    """
    subjects_synced = await store.sync_subjects(user["user_id"], body.subjects)
    sessions_synced = await store.sync_sessions(user["user_id"], body.sessions)
    reviews_synced = await store.sync_reviews(user["user_id"], body.review_schedule)

    return SyncResponse(
        subjects_synced=subjects_synced,
        sessions_synced=sessions_synced,
        reviews_synced=reviews_synced,
    )
