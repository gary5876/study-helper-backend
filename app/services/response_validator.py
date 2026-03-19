"""Validates and sanitises LLM-generated content."""
from __future__ import annotations

import logging
import re
import uuid
from collections import Counter

from app.core.exceptions import ValidationError
from app.models.schemas import (
    FillQuestion,
    GlossaryEntry,
    KeyConcept,
    MCQOptions,
    MCQQuestion,
    StudyNotes,
    StudySection,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────

def _tokenize(text: str) -> set[str]:
    """Simple word tokenizer for keyword overlap checks."""
    return set(re.findall(r"\b[a-zA-Z]{4,}\b", text.lower()))


def _keyword_overlap(text_a: str, text_b: str) -> float:
    """Return fraction of text_a's keywords present in text_b."""
    a = _tokenize(text_a)
    b = _tokenize(text_b)
    if not a:
        return 1.0
    return len(a & b) / len(a)


def _ensure_id(obj: dict) -> dict:
    """Ensure the dict has an 'id' field."""
    if not obj.get("id"):
        obj["id"] = str(uuid.uuid4())
    return obj


# ─────────────────────────────────────────
# Notes Validation
# ─────────────────────────────────────────

def validate_notes(raw: dict, source_text: str) -> StudyNotes:
    """
    Validate and coerce the raw notes dict into a StudyNotes model.
    Raises ValidationError if the structure is unrecoverable.
    """
    if not isinstance(raw, dict):
        raise ValidationError("Notes response is not a JSON object.")

    # key_concepts
    raw_concepts = raw.get("key_concepts", [])
    if not isinstance(raw_concepts, list) or len(raw_concepts) == 0:
        raise ValidationError("No key_concepts found in generated notes.")

    concepts: list[KeyConcept] = []
    for c in raw_concepts:
        if not isinstance(c, dict):
            continue
        _ensure_id(c)
        importance = c.get("importance", "medium")
        if importance not in ("high", "medium", "low"):
            importance = "medium"
        concepts.append(
            KeyConcept(
                id=c["id"],
                term=str(c.get("term", "Unknown")),
                definition=str(c.get("definition", "")),
                importance=importance,
            )
        )

    # sections
    raw_sections = raw.get("sections", [])
    sections: list[StudySection] = []
    for s in raw_sections:
        if not isinstance(s, dict):
            continue
        bullets = s.get("bullets", [])
        if not isinstance(bullets, list):
            bullets = []
        sections.append(
            StudySection(
                title=str(s.get("title", "Section")),
                summary=str(s.get("summary", "")),
                bullets=[str(b) for b in bullets],
            )
        )
    if not sections:
        # Fallback: create one section from full source
        sections.append(
            StudySection(
                title="Document Summary",
                summary="Auto-generated summary.",
                bullets=[],
            )
        )

    # glossary
    raw_glossary = raw.get("glossary", [])
    glossary: list[GlossaryEntry] = []
    for g in raw_glossary:
        if not isinstance(g, dict):
            continue
        glossary.append(
            GlossaryEntry(
                term=str(g.get("term", "")),
                brief_def=str(g.get("brief_def", "")),
            )
        )

    return StudyNotes(key_concepts=concepts, sections=sections, glossary=glossary)


# ─────────────────────────────────────────
# MCQ Validation
# ─────────────────────────────────────────

def validate_mcq(raw: dict, valid_concept_ids: set[str], source_text: str) -> list[MCQQuestion]:
    """
    Validate MCQ questions. Skips (logs) invalid questions rather than failing.
    Deduplicates near-identical questions (simple title overlap heuristic).
    """
    questions_raw = raw.get("questions", [])
    if not isinstance(questions_raw, list):
        raise ValidationError("MCQ response missing 'questions' array.")

    seen_questions: list[str] = []
    validated: list[MCQQuestion] = []

    for q in questions_raw:
        if not isinstance(q, dict):
            continue
        _ensure_id(q)

        # Required fields
        question_text = str(q.get("question", "")).strip()
        if not question_text:
            continue

        options_raw = q.get("options", {})
        if not isinstance(options_raw, dict):
            continue
        # All four options must be present and non-empty
        opt_values = [str(options_raw.get(k, "")).strip() for k in ("A", "B", "C", "D")]
        if any(v == "" for v in opt_values):
            logger.warning("MCQ %s: missing option(s), skipping.", q["id"])
            continue
        # Options must be distinct
        if len(set(opt_values)) < 4:
            logger.warning("MCQ %s: duplicate options, skipping.", q["id"])
            continue

        correct = str(q.get("correct_answer", "")).upper()
        if correct not in ("A", "B", "C", "D"):
            logger.warning("MCQ %s: invalid correct_answer '%s', skipping.", q["id"], correct)
            continue

        explanation = str(q.get("explanation", "")).strip()
        if len(explanation) < 20:
            explanation = f"The correct answer is {correct}."

        # Hallucination check on explanation
        overlap = _keyword_overlap(explanation, source_text)
        if len(explanation.split()) > 30 and overlap < 0.15:
            logger.warning(
                "MCQ %s: explanation keyword overlap=%.2f (possible hallucination).", q["id"], overlap
            )
            # Don't skip — just log. Partial result is better than empty.

        # Concept linkage
        concept_id = str(q.get("concept_id", ""))
        if concept_id not in valid_concept_ids:
            concept_id = next(iter(valid_concept_ids), "")

        # Near-duplicate check (simple: if > 70% token overlap with an existing question)
        q_tokens = _tokenize(question_text)
        is_dup = False
        for prev in seen_questions:
            prev_tokens = _tokenize(prev)
            union = q_tokens | prev_tokens
            if union and len(q_tokens & prev_tokens) / len(union) > 0.70:
                is_dup = True
                break
        if is_dup:
            logger.warning("MCQ %s: near-duplicate, skipping.", q["id"])
            continue
        seen_questions.append(question_text)

        difficulty = str(q.get("difficulty", "medium"))
        if difficulty not in ("easy", "medium", "hard"):
            difficulty = "medium"

        validated.append(
            MCQQuestion(
                id=q["id"],
                question=question_text,
                options=MCQOptions(
                    A=opt_values[0],
                    B=opt_values[1],
                    C=opt_values[2],
                    D=opt_values[3],
                ),
                correct_answer=correct,  # type: ignore[arg-type]
                explanation=explanation,
                concept_id=concept_id,
                difficulty=difficulty,  # type: ignore[arg-type]
            )
        )

    if not validated:
        raise ValidationError("No valid MCQ questions could be extracted from the LLM response.")

    return validated


# ─────────────────────────────────────────
# Fill-in-blank Validation
# ─────────────────────────────────────────

def validate_fill(raw: dict, valid_concept_ids: set[str], source_text: str) -> list[FillQuestion]:
    """Validate fill-in-the-blank questions."""
    questions_raw = raw.get("questions", [])
    if not isinstance(questions_raw, list):
        raise ValidationError("Fill response missing 'questions' array.")

    validated: list[FillQuestion] = []

    for q in questions_raw:
        if not isinstance(q, dict):
            continue
        _ensure_id(q)

        sentence = str(q.get("sentence_with_blank", "")).strip()
        if "___" not in sentence:
            logger.warning("Fill %s: no ___ blank found, skipping.", q["id"])
            continue
        if sentence.count("___") > 1:
            # Keep only first blank
            parts = sentence.split("___")
            sentence = parts[0] + "___" + "___".join(parts[1:]).replace("___", "")

        answer = str(q.get("answer", "")).strip()
        if not answer:
            logger.warning("Fill %s: empty answer, skipping.", q["id"])
            continue

        variants = q.get("acceptable_variants", [])
        if not isinstance(variants, list):
            variants = []
        variants = [str(v).strip() for v in variants if str(v).strip()]

        hint = str(q.get("hint", "")).strip()

        concept_id = str(q.get("concept_id", ""))
        if concept_id not in valid_concept_ids:
            concept_id = next(iter(valid_concept_ids), "")

        validated.append(
            FillQuestion(
                id=q["id"],
                sentence_with_blank=sentence,
                answer=answer,
                acceptable_variants=variants,
                hint=hint,
                concept_id=concept_id,
            )
        )

    if not validated:
        raise ValidationError("No valid fill-in-blank questions could be extracted.")

    return validated
