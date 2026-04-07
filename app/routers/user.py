"""User data API — sessions, subjects, review schedule, sync."""
from __future__ import annotations

import logging
from typing import Optional
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import require_current_user
from app.services.user_store import UserStore, get_user_store

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/user", tags=["user"])


# ─────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────

class SubjectCreate(BaseModel):
    name: str
    color: str = "#6c63ff"


class SubjectResponse(BaseModel):
    id: str
    name: str
    color: str
    created_at: datetime


class SessionCreate(BaseModel):
    pdf_name: str
    pdf_hash: Optional[str] = None
    subject_id: Optional[str] = None
    page_count: int = 0
    word_count: int = 0
    status: str = "pending"


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
    session_id: str
    question_id: str
    question_type: str
    interval_days: int = 1
    next_review_at: datetime
    ease_factor: float = 2.5
    repetitions: int = 0
    status: str = "pending"


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
