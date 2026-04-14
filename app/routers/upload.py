"""POST /upload — accept PDF, parse it, store in session store."""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from typing import Optional

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.exceptions import PDFParseError
from app.models.schemas import UploadResponse
from app.services.pdf_parser import parse_pdf
from app.services.session_store import SessionRecord, get_store
from app.services.user_store import get_user_store

logger = logging.getLogger(__name__)
router = APIRouter()
settings = get_settings()

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "binary/octet-stream",  # some browsers
}


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    api_key: Optional[str] = Form(default=None),  # DEPRECATED: use X-API-Key header instead
    plan: str = Form(default="paid"),
    subject_id: Optional[str] = Form(default=None),
    user: Optional[dict] = Depends(get_current_user),
):
    """
    Upload a PDF file. Returns a session_id for subsequent /generate calls.

    Pass the Anthropic API key via the `X-API-Key` header (preferred) or the `api_key` form field.
    Free plan users do not need to provide an API key.
    """
    resolved_key = (x_api_key or api_key or "").strip()
    if api_key and not x_api_key:
        logger.warning("DEPRECATED: API key received via form body instead of X-API-Key header")
    if plan == "paid" and (not resolved_key or len(resolved_key) < 10):
        raise HTTPException(status_code=400, detail="A valid Anthropic API key is required.")
    api_key = resolved_key

    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    # Sanitize filename: strip directory components and unsafe characters
    filename = os.path.basename(filename)
    filename = re.sub(r'[^\w\s\-.]', '_', filename)
    if not filename.lower().endswith(".pdf"):
        filename = "document.pdf"

    # Validate MIME type
    if file.content_type and file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail="Only PDF files are accepted.")

    file_bytes = await file.read()

    if len(file_bytes) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    # Parse PDF (validates structure, detects scanned / password-protected)
    try:
        doc = parse_pdf(file_bytes, filename)
    except PDFParseError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    tentative_id = str(uuid.uuid4())
    pdf_hash = hashlib.sha256(file_bytes).hexdigest()

    # 로그인 상태면 user_sessions에 upsert하고, 반환된 id를 권위 있는 session_id로 사용.
    # 같은 PDF 재업로드 시 기존 행의 id를 그대로 재사용하여 메모리 store와 DB row가
    # 항상 동일한 id를 공유하도록 한다.
    session_id = tentative_id
    if user:
        try:
            from app.routers.user import SessionCreate
            user_store = get_user_store()
            row = await user_store.upsert_session(user["user_id"], SessionCreate(
                id=tentative_id,
                pdf_name=filename,
                pdf_hash=pdf_hash,
                subject_id=subject_id,
                page_count=doc.page_count,
                word_count=doc.word_count,
                status="pending",
            ))
            session_id = row["id"]
        except Exception as exc:
            logger.warning("Cloud session save failed for user %s: %s", user["user_id"], exc)

    # Store record (PDF bytes not persisted server-side in dev mode — just metadata)
    record = SessionRecord(
        session_id=session_id,
        pdf_name=filename,
        page_count=doc.page_count,
        word_count=doc.word_count,
        s3_key=f"uploads/{session_id}/{filename}",
        pdf_hash=pdf_hash,
        user_id=user["user_id"] if user else None,
        status="uploaded",
    )
    # Attach parsed text to record so /generate can use it without re-parsing
    # We serialise the full_text into a temp field on the store record
    record_with_text = record
    # Store the parsed doc text as part of session (in result_json temporarily)
    import json
    record_with_text.result_json = json.dumps({
        "full_text": doc.full_text,
        "sections": [
            {"title": s.title, "content": s.content, "page_range": list(s.page_range)}
            for s in doc.sections
        ],
        "warning": doc.warning,
    })

    store = get_store()
    await store.save(record_with_text)

    if doc.warning:
        logger.warning("Upload %s: %s", session_id, doc.warning)

    return UploadResponse(
        session_id=session_id,
        pdf_name=filename,
        page_count=doc.page_count,
        word_count=doc.word_count,
        status="uploaded",
    )
