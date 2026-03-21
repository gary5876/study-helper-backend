from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field
import uuid


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
    difficulty: Literal["easy", "medium", "hard"] = "medium"


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
    api_key: Optional[str] = None  # kept for backwards-compat; prefer X-API-Key header
    options: Optional[GenerateOptions] = None


class GenerateOptions(BaseModel):
    mcq_count: Optional[int] = None   # None = auto-calculate
    fill_count: Optional[int] = None  # None = auto-calculate


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
