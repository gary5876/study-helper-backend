"""POST /generate, GET /status, GET /result, DELETE /session endpoints."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException
from typing import Optional

from app.core.config import get_settings
from app.core.exceptions import GenerationError, ValidationError
from app.models.schemas import (
    ContentMetadata,
    DeleteResponse,
    GenerateOptions,
    GenerateRequest,
    GenerateResponse,
    StatusResponse,
    StudyContent,
)
from app.services.anthropic_client import generate_with_retry
from app.services.llm_provider import GeminiClient
from app.services.prompt_builder import (
    build_fill_prompt,
    build_mcq_prompt,
    build_notes_prompt,
    calculate_question_counts,
)
from app.services.response_validator import validate_fill, validate_mcq, validate_notes
from app.services.session_store import get_store

settings = get_settings()
logger = logging.getLogger(__name__)
router = APIRouter()


async def _run_generation(session_id: str, api_key: str, options: GenerateOptions | None, plan: str = "paid"):
    """Background task: run the full generation pipeline and persist result."""
    store = get_store()

    # Retrieve session
    record = await store.get(session_id)
    if record is None:
        logger.error("Generation task: session %s not found.", session_id)
        return

    # Load pre-parsed doc from the temporary result_json field
    try:
        parsed = json.loads(record.result_json or "{}")
    except Exception:
        await store.update_status(session_id, "failed", error_message="Failed to load parsed document.")
        return

    full_text = parsed.get("full_text", "")
    sections = parsed.get("sections", [])
    section_count = max(1, len(sections))

    if not full_text:
        await store.update_status(session_id, "failed", error_message="Document text is empty.")
        return

    # Determine question counts
    mcq_count = options.mcq_count if options and options.mcq_count else None
    fill_count = options.fill_count if options and options.fill_count else None
    if mcq_count is None or fill_count is None:
        auto_mcq, auto_fill = calculate_question_counts(section_count)
        mcq_count = mcq_count or auto_mcq
        fill_count = fill_count or auto_fill

    await store.update_status(session_id, "processing", progress_pct=5)

    # Build prompts
    sys_notes, prompt_notes = build_notes_prompt(full_text)
    await store.update_status(session_id, "processing", progress_pct=10)

    # ── Stage 1: Notes generation ──────────────────────────────────────
    if plan == "free":
        gemini = GeminiClient()

    try:
        if plan == "free":
            notes_raw = await gemini.generate_notes(sys_notes, prompt_notes)
        else:
            notes_raw = await generate_with_retry(api_key, sys_notes, prompt_notes, max_tokens=4096)
    except GenerationError as exc:
        await store.update_status(session_id, "failed", error_message=f"Notes generation failed: {exc.message}")
        return

    await store.update_status(session_id, "processing", progress_pct=40)

    # Validate notes to get concept IDs for MCQ/fill prompts
    try:
        notes_obj = validate_notes(notes_raw, full_text)
    except ValidationError as exc:
        await store.update_status(session_id, "failed", error_message=f"Notes validation failed: {exc.message}")
        return

    notes_dict = notes_raw  # keep raw dict for prompt context

    # ── Stage 2: MCQ generation ─────────────────────────────────────────
    if plan == "free":
        # Batched: MCQ_BATCH_SIZE questions per call to stay within Gemini output limits.
        # Notes are complete here, so concept IDs are correctly populated.
        try:
            mcq_raw = await gemini.generate_mcq_batched(full_text, notes_dict, mcq_count)
        except GenerationError as exc:
            await store.update_status(session_id, "failed", error_message=f"MCQ generation failed: {exc.message}")
            return
    else:
        sys_mcq, prompt_mcq = build_mcq_prompt(full_text, notes_dict, mcq_count)
        try:
            mcq_raw = await generate_with_retry(api_key, sys_mcq, prompt_mcq, max_tokens=8192)
        except GenerationError as exc:
            await store.update_status(session_id, "failed", error_message=f"MCQ generation failed: {exc.message}")
            return

    await store.update_status(session_id, "processing", progress_pct=70)

    # ── Stage 3: Fill-in-blank generation ──────────────────────────────
    if plan == "free":
        try:
            fill_raw = await gemini.generate_fill_batched(full_text, notes_dict, fill_count)
        except GenerationError as exc:
            await store.update_status(session_id, "failed", error_message=f"Fill generation failed: {exc.message}")
            return
    else:
        sys_fill, prompt_fill = build_fill_prompt(full_text, notes_dict, fill_count)
        try:
            fill_raw = await generate_with_retry(api_key, sys_fill, prompt_fill, max_tokens=2048)
        except GenerationError as exc:
            await store.update_status(session_id, "failed", error_message=f"Fill generation failed: {exc.message}")
            return

    await store.update_status(session_id, "processing", progress_pct=85)

    # Validate
    valid_concept_ids = {c.id for c in notes_obj.key_concepts}

    try:
        mcq_list = validate_mcq(mcq_raw, valid_concept_ids, full_text)
        fill_list = validate_fill(fill_raw, valid_concept_ids, full_text)
    except ValidationError as exc:
        await store.update_status(session_id, "failed", error_message=f"Validation failed: {exc.message}")
        return

    # Build final StudyContent
    content = StudyContent(
        session_id=session_id,
        notes=notes_obj,
        mcq_questions=mcq_list,
        fill_questions=fill_list,
        metadata=ContentMetadata(
            page_count=record.page_count,
            word_count=record.word_count,
            generated_at=datetime.now(timezone.utc).isoformat(),
            model_used=settings.GEMINI_MODEL if plan == "free" else settings.ANTHROPIC_MODEL,
            section_count=section_count,
        ),
    )

    await store.update_status(
        session_id,
        "complete",
        progress_pct=100,
        result_json=content.model_dump_json(),
    )
    logger.info("Generation complete for session %s: %d MCQ, %d fill", session_id, len(mcq_list), len(fill_list))


# ─────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────

@router.post("/generate", response_model=GenerateResponse)
async def start_generation(
    body: GenerateRequest,
    background_tasks: BackgroundTasks,
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
):
    """Kick off async content generation for an uploaded session.

    Pass the Anthropic API key via the `X-API-Key` header (preferred) or the `api_key` body field.
    """
    resolved_key = (x_api_key or body.api_key or "").strip()
    if body.plan == "paid" and (not resolved_key or len(resolved_key) < 10):
        raise HTTPException(status_code=400, detail="A valid Anthropic API key is required.")

    store = get_store()
    record = await store.get(body.session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Session '{body.session_id}' not found.")
    if record.status == "complete":
        return GenerateResponse(session_id=body.session_id, status="complete")
    if record.status == "processing":
        return GenerateResponse(session_id=body.session_id, status="processing")

    background_tasks.add_task(_run_generation, body.session_id, resolved_key, body.options, body.plan)
    return GenerateResponse(session_id=body.session_id, status="processing")


@router.get("/status/{session_id}", response_model=StatusResponse)
async def get_status(session_id: str):
    """Poll generation progress."""
    store = get_store()
    record = await store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return StatusResponse(
        session_id=session_id,
        status=record.status,  # type: ignore[arg-type]
        progress_pct=record.progress_pct,
        error_message=record.error_message,
    )


@router.get("/result/{session_id}", response_model=StudyContent)
async def get_result(session_id: str):
    """Retrieve the completed StudyContent. Returns 202 if still processing."""
    store = get_store()
    record = await store.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    if record.status == "processing":
        raise HTTPException(status_code=202, detail="Generation still in progress.")
    if record.status == "failed":
        raise HTTPException(status_code=500, detail=record.error_message or "Generation failed.")
    if record.status == "uploaded":
        raise HTTPException(status_code=400, detail="Generation not started. Call /generate first.")
    if not record.result_json:
        raise HTTPException(status_code=500, detail="Result data missing.")

    try:
        return StudyContent.model_validate_json(record.result_json)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse result: {exc}") from exc


@router.delete("/session/{session_id}", response_model=DeleteResponse)
async def delete_session(session_id: str):
    """Delete a session and its associated data."""
    store = get_store()
    deleted = await store.delete(session_id)
    return DeleteResponse(deleted=deleted, session_id=session_id)
