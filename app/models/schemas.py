from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator
import uuid


# ─────────────────────────────────────────
# Constants
# ─────────────────────────────────────────

_DIFFICULTY_TO_LEVEL: dict[str, int] = {"easy": 2, "medium": 3, "hard": 4}
_VALID_QUESTION_TYPES = {"concept", "application"}


# ─────────────────────────────────────────
# Study Notes
# ─────────────────────────────────────────

class KeyConcept(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    term: str
    definition: str
    importance: Literal["high", "medium", "low"] = "medium"


class StudySection(BaseModel):
    title: str
    summary: str
    bullets: list[str]


class GlossaryEntry(BaseModel):
    term: str
    brief_def: str


class StudyNotes(BaseModel):
    key_concepts: list[KeyConcept]
    sections: list[StudySection]
    glossary: list[GlossaryEntry]


# ─────────────────────────────────────────
# MCQ Questions
# ─────────────────────────────────────────

class MCQOptions(BaseModel):
    A: str
    B: str
    C: str
    D: str


class MCQQuestion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    question: str
    options: MCQOptions
    correct_answer: Literal["A", "B", "C", "D"]
    explanation: str
    concept_id: str
    level: int = 3                                          # 1–5 (replaces difficulty)
    question_type: Literal["concept", "application"] = "concept"

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        # Backward compat: old data has 'difficulty' string, no 'level'
        if "level" not in data and "difficulty" in data:
            data["level"] = _DIFFICULTY_TO_LEVEL.get(str(data["difficulty"]), 3)
        # Clamp level to 1–5
        if "level" in data:
            try:
                data["level"] = max(1, min(5, int(data["level"])))
            except (ValueError, TypeError):
                data["level"] = 3
        # Coerce invalid question_type
        if data.get("question_type") not in _VALID_QUESTION_TYPES:
            data["question_type"] = "concept"
        return data


# ─────────────────────────────────────────
# Fill-in-the-blank Questions
# ─────────────────────────────────────────

class FillQuestion(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sentence_with_blank: str
    answer: str
    acceptable_variants: list[str] = Field(default_factory=list)
    hint: str = ""
    concept_id: str
    level: int = 3                                          # 1–5
    question_type: Literal["concept", "application"] = "concept"

    @model_validator(mode="before")
    @classmethod
    def _coerce_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if "level" not in data and "difficulty" in data:
            data["level"] = _DIFFICULTY_TO_LEVEL.get(str(data["difficulty"]), 3)
        if "level" in data:
            try:
                data["level"] = max(1, min(5, int(data["level"])))
            except (ValueError, TypeError):
                data["level"] = 3
        if data.get("question_type") not in _VALID_QUESTION_TYPES:
            data["question_type"] = "concept"
        return data


# ─────────────────────────────────────────
# Full Study Content Response
# ─────────────────────────────────────────

class ContentMetadata(BaseModel):
    page_count: int
    word_count: int
    generated_at: str
    model_used: str
    section_count: int


class StudyContent(BaseModel):
    session_id: str
    notes: StudyNotes
    mcq_questions: list[MCQQuestion]
    fill_questions: list[FillQuestion]
    metadata: ContentMetadata


# ─────────────────────────────────────────
# API Request / Response Shapes
# ─────────────────────────────────────────

class UploadResponse(BaseModel):
    session_id: str
    pdf_name: str
    page_count: int
    word_count: int
    status: str = "uploaded"


class GenerateRequest(BaseModel):
    session_id: str
    plan: Literal["paid", "gpt", "timely"] = "paid"
    api_key: Optional[str] = None  # kept for backwards-compat; prefer X-API-Key header
    options: Optional[GenerateOptions] = None
    lang: Literal["ko", "en"] = "ko"


class GenerateOptions(BaseModel):
    mcq_count: Optional[int] = Field(default=None, ge=1, le=50)
    fill_count: Optional[int] = Field(default=None, ge=1, le=50)
    model: Optional[str] = Field(default=None, max_length=100, pattern=r'^[a-zA-Z0-9.\-_]+$')


class GenerateResponse(BaseModel):
    session_id: str
    status: str = "processing"


class StatusResponse(BaseModel):
    session_id: str
    status: Literal["uploaded", "processing", "complete", "failed"]
    progress_pct: int
    error_message: Optional[str] = None


class DeleteResponse(BaseModel):
    deleted: bool
    session_id: str


# Fix forward reference
GenerateRequest.model_rebuild()
