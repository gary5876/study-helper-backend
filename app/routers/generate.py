"""POST /generate, GET /status, GET /result, DELETE /session endpoints."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from typing import Optional

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.exceptions import GenerationError, ValidationError
from app.core.validators import is_valid_uuid, validate_api_key
from app.models.schemas import (
    ContentMetadata,
    DeleteResponse,
    GenerateOptions,
    GenerateRequest,
    GenerateResponse,
    StatusResponse,
    StudyContent,
)
from app.services.anthropic_client import generate_with_retry as anthropic_generate
from app.services.openai_client import generate_with_retry as openai_generate
from app.services.timely_client import generate_with_retry as timely_generate
from app.services.prompt_builder import (
    build_fill_prompt,
    build_mcq_prompt,
    build_notes_prompt,
    build_ox_prompt,
    calculate_question_counts,
)
from app.services.response_validator import validate_fill, validate_mcq, validate_notes, validate_ox
from app.services.session_store import get_store
from app.services.question_bank import get_cached, save_to_bank
from app.services.user_store import get_user_store

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter()


async def _sync_user_session_status(user_id: str | None, session_id: str, status: str) -> bool:
    """user_sessions DB 행의 status를 갱신. 로그인 사용자가 아니면 True 반환.

    실패(예외 발생 또는 0 row matched)는 error 로그로 남기고 False 반환.
    호출측이 반환값을 확인해 memory store를 failed로 전이시키는 것이 원칙.
    """
    if not user_id:
        return True
    try:
        updated = await get_user_store().update_session_status(user_id, session_id, status)
    except Exception as exc:
        logger.error(
            "user_sessions status sync 실패 (session=%s user=%s status=%s): %s",
            session_id, user_id, status, exc,
        )
        return False
    if not updated:
        logger.error(
            "user_sessions status sync 0 row matched (session=%s user=%s status=%s)",
            session_id, user_id, status,
        )
        return False
    return True


async def _run_generation(session_id: str, api_key: str, options: GenerateOptions | None, plan: str = "paid", lang: str = "ko"):
    """Background task: run the full generation pipeline and persist result."""
    store = get_store()

    # Retrieve session
    record = await store.get(session_id)
    if record is None:
        logger.error("Generation task: session %s not found.", session_id)
        return

    async def _fail(msg: str) -> None:
        await store.update_status(session_id, "failed", error_message=msg)
        await _sync_user_session_status(record.user_id, session_id, "failed")

    async def _finalize_ready(result_json: str) -> None:
        """메모리 store 완료 처리 + DB sync. sync 실패 시 memory를 failed로 되돌림."""
        await store.update_status(session_id, "complete", progress_pct=100, result_json=result_json)
        ok = await _sync_user_session_status(record.user_id, session_id, "ready")
        if not ok:
            await store.update_status(
                session_id,
                "failed",
                error_message="DB 상태 동기화 실패 — 세션을 다시 생성해주세요.",
                result_json=result_json,
            )

    # Check question bank cache first
    cached_json = await get_cached(record.pdf_hash)
    if cached_json:
        await _finalize_ready(cached_json)
        logger.info("Question bank cache hit for session %s (hash=%s)", session_id, record.pdf_hash[:12])
        return

    # Load pre-parsed doc from the temporary result_json field
    try:
        parsed = json.loads(record.result_json or "{}")
    except Exception:
        await _fail("Failed to load parsed document.")
        return

    full_text = parsed.get("full_text", "")
    sections = parsed.get("sections", [])
    section_count = max(1, len(sections))

    if not full_text:
        await _fail("Document text is empty.")
        return

    # Determine question counts
    mcq_count = options.mcq_count if options and options.mcq_count else None
    fill_count = options.fill_count if options and options.fill_count else None
    ox_count = options.ox_count if options and options.ox_count is not None else None
    if mcq_count is None or fill_count is None or ox_count is None:
        auto_mcq, auto_fill, auto_ox = calculate_question_counts(section_count)
        mcq_count = mcq_count or auto_mcq
        fill_count = fill_count or auto_fill
        ox_count = auto_ox if ox_count is None else ox_count

    # Resolve model: use client-specified model if provided, else server default
    model = options.model if options and options.model else None

    await store.update_status(session_id, "processing", progress_pct=5)

    # Build prompts
    sys_notes, prompt_notes = build_notes_prompt(full_text, lang)
    await store.update_status(session_id, "processing", progress_pct=10)

    # ── Stage 1: Notes generation ──────────────────────────────────────
    try:
        if plan == "gpt":
            notes_raw = await openai_generate(api_key, sys_notes, prompt_notes, max_tokens=4096, model=model)
        elif plan == "timely":
            notes_raw = await timely_generate(api_key, sys_notes, prompt_notes, max_tokens=4096, model=model)
        else:
            notes_raw = await anthropic_generate(api_key, sys_notes, prompt_notes, max_tokens=4096, model=model)
    except GenerationError as exc:
        await _fail(f"Notes generation failed: {exc.message}")
        return

    await store.update_status(session_id, "processing", progress_pct=30)

    # Validate notes to get concept IDs for MCQ/fill/ox prompts
    try:
        notes_obj = validate_notes(notes_raw, full_text)
    except ValidationError as exc:
        await _fail(f"Notes validation failed: {exc.message}")
        return

    notes_dict = notes_raw  # keep raw dict for prompt context

    async def _call_llm(sys_p: str, user_p: str, max_tokens: int) -> dict:
        if plan == "gpt":
            return await openai_generate(api_key, sys_p, user_p, max_tokens=max_tokens, model=model)
        if plan == "timely":
            return await timely_generate(api_key, sys_p, user_p, max_tokens=max_tokens, model=model)
        return await anthropic_generate(api_key, sys_p, user_p, max_tokens=max_tokens, model=model)

    # ── Stage 2: OX generation (skipped if ox_count == 0) ──────────────
    ox_raw: dict | None = None
    if ox_count and ox_count > 0:
        sys_ox, prompt_ox = build_ox_prompt(full_text, notes_dict, ox_count, lang)
        try:
            ox_raw = await _call_llm(sys_ox, prompt_ox, max_tokens=4096)
        except GenerationError as exc:
            await _fail(f"OX generation failed: {exc.message}")
            return
        await store.update_status(session_id, "processing", progress_pct=50)

    # ── Stage 3: MCQ generation ─────────────────────────────────────────
    sys_mcq, prompt_mcq = build_mcq_prompt(full_text, notes_dict, mcq_count, lang)
    try:
        mcq_raw = await _call_llm(sys_mcq, prompt_mcq, max_tokens=8192)
    except GenerationError as exc:
        await _fail(f"MCQ generation failed: {exc.message}")
        return

    await store.update_status(session_id, "processing", progress_pct=75)

    # ── Stage 4: Fill-in-blank generation ──────────────────────────────
    sys_fill, prompt_fill = build_fill_prompt(full_text, notes_dict, fill_count, lang)
    try:
        fill_raw = await _call_llm(sys_fill, prompt_fill, max_tokens=2048)
    except GenerationError as exc:
        await _fail(f"Fill generation failed: {exc.message}")
        return

    await store.update_status(session_id, "processing", progress_pct=88)

    # Validate
    valid_concept_ids = {c.id for c in notes_obj.key_concepts}

    try:
        mcq_list = validate_mcq(mcq_raw, valid_concept_ids, full_text)
        fill_list = validate_fill(fill_raw, valid_concept_ids, full_text)
        ox_list = validate_ox(ox_raw, valid_concept_ids, full_text) if ox_raw else []
    except ValidationError as exc:
        await _fail(f"Validation failed: {exc.message}")
        return

    # Build final StudyContent
    content = StudyContent(
        session_id=session_id,
        notes=notes_obj,
        mcq_questions=mcq_list,
        fill_questions=fill_list,
        ox_questions=ox_list,
        metadata=ContentMetadata(
            page_count=record.page_count,
            word_count=record.word_count,
            generated_at=datetime.now(timezone.utc).isoformat(),
            model_used=model or (
                settings.OPENAI_MODEL if plan == "gpt"
                else settings.TIMELY_MODEL if plan == "timely"
                else settings.ANTHROPIC_MODEL
            ),
            section_count=section_count,
        ),
    )

    content_json = content.model_dump_json()
    await _finalize_ready(content_json)
    logger.info(
        "Generation complete for session %s: %d MCQ, %d fill, %d OX",
        session_id, len(mcq_list), len(fill_list), len(ox_list),
    )

    # Persist to question bank for future cache hits
    await save_to_bank(
        pdf_hash=record.pdf_hash,
        pdf_name=record.pdf_name,
        page_count=record.page_count,
        word_count=record.word_count,
        content_json=content_json,
    )


# ─────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────

def _check_ownership(record, user: dict | None) -> None:
    """Raise 403 if the caller is not allowed to access this session."""
    if record.user_id is None:
        # Guest session: only unauthenticated (guest) callers may access
        if user is not None:
            raise HTTPException(status_code=403, detail="해당 세션에 대한 접근 권한이 없습니다")
    else:
        # Owned session: only the owner may access
        if user is None or user["user_id"] != record.user_id:
            raise HTTPException(status_code=403, detail="해당 세션에 대한 접근 권한이 없습니다")


@router.post("/generate", response_model=GenerateResponse)
async def start_generation(
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    user: Optional[dict] = Depends(get_current_user),
):
    """Kick off async content generation for an uploaded session.

    Pass the Anthropic API key via the `X-API-Key` header (preferred) or the `api_key` body field.
    """
    resolved_key = (x_api_key or body.api_key or "").strip()
    if body.plan == "paid" and not validate_api_key(resolved_key, "paid"):
        raise HTTPException(status_code=400, detail="A valid Anthropic API key is required.")
    if body.plan == "gpt" and not validate_api_key(resolved_key, "gpt"):
        raise HTTPException(status_code=400, detail="A valid OpenAI API key is required.")
    if body.plan == "timely" and not validate_api_key(resolved_key, "timely"):
        raise HTTPException(status_code=400, detail="A valid TimelyGPT API key is required.")

    if not is_valid_uuid(body.session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    store = get_store()
    record = await store.get(body.session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    _check_ownership(record, user)
    if record.status == "complete":
        return GenerateResponse(session_id=body.session_id, status="complete")
    if record.status == "processing":
        return GenerateResponse(session_id=body.session_id, status="processing")

    background_tasks.add_task(_run_generation, body.session_id, resolved_key, body.options, body.plan, body.lang)
    return GenerateResponse(session_id=body.session_id, status="processing")


@router.get("/status/{session_id}", response_model=StatusResponse)
async def get_status(session_id: str, user: Optional[dict] = Depends(get_current_user)):
    """Poll generation progress."""
    if not is_valid_uuid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    store = get_store()
    record = await store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    _check_ownership(record, user)
    return StatusResponse(
        session_id=session_id,
        status=record.status,  # type: ignore[arg-type]
        progress_pct=record.progress_pct,
        error_message=record.error_message,
    )


async def _load_result_from_db(session_id: str, user_id: str) -> str | None:
    """memory store miss 시 DB fallback: user_sessions.pdf_hash → question_bank.content_json.

    로그인 사용자 소유 세션만 조회. 두 테이블이 서로 다른 DB 풀이어도 각자 조회한다.
    """
    try:
        pool = getattr(get_user_store(), "_pool", None)
        if pool is None:
            return None
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT pdf_hash, status FROM user_sessions "
                "WHERE id = $1::uuid AND user_id = $2::uuid",
                session_id, user_id,
            )
        if row is None or not row["pdf_hash"]:
            return None
        # question_bank는 별개 풀이므로 get_cached 사용
        cached = await get_cached(row["pdf_hash"])
        return cached
    except Exception as exc:
        logger.warning("DB fallback load failed for session %s: %s", session_id, exc)
        return None


@router.get("/result/{session_id}", response_model=StudyContent)
async def get_result(session_id: str, user: Optional[dict] = Depends(get_current_user)):
    """Retrieve the completed StudyContent. Returns 202 if still processing."""
    if not is_valid_uuid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    store = get_store()
    record = await store.get(session_id)

    # Fast path: memory store hit
    if record is not None:
        _check_ownership(record, user)
        if record.status == "processing":
            raise HTTPException(status_code=202, detail="Generation still in progress.")
        if record.status == "failed":
            raise HTTPException(status_code=500, detail=record.error_message or "Generation failed.")
        if record.status == "uploaded":
            raise HTTPException(status_code=400, detail="Generation not started. Call /generate first.")
        if record.result_json:
            try:
                return StudyContent.model_validate_json(record.result_json)
            except Exception as exc:
                logger.error("Failed to parse result for session %s: %s", session_id, exc)
                raise HTTPException(status_code=500, detail="Failed to parse result.") from exc

    # Fallback: memory store miss (TTL 만료/서버 재시작 등). DB에서 pdf_hash 조회 후 question_bank에서 로드.
    if user is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    cached = await _load_result_from_db(session_id, user["user_id"])
    if cached is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    try:
        return StudyContent.model_validate_json(cached)
    except Exception as exc:
        logger.error("Failed to parse DB-cached result for session %s: %s", session_id, exc)
        raise HTTPException(status_code=500, detail="Failed to parse result.") from exc


@router.delete("/session/{session_id}", response_model=DeleteResponse)
async def delete_session(session_id: str, user: Optional[dict] = Depends(get_current_user)):
    """Delete a session and its associated data."""
    if not is_valid_uuid(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID format.")
    store = get_store()
    record = await store.get(session_id)
    if record is None:
        return DeleteResponse(deleted=False, session_id=session_id)
    _check_ownership(record, user)
    deleted = await store.delete(session_id)
    return DeleteResponse(deleted=deleted, session_id=session_id)
