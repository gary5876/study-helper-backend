"""Unit tests for the response validator."""
import pytest

from app.core.exceptions import ValidationError
from app.services.response_validator import validate_notes, validate_mcq, validate_fill


SOURCE_TEXT = (
    "Machine learning is a subset of artificial intelligence. "
    "Neural networks are computational models inspired by the brain. "
    "Gradient descent is an optimization algorithm used in training. "
    "Supervised learning uses labeled training data. "
    "Backpropagation computes gradients efficiently. "
    "Deep learning models have multiple hidden layers. "
)

VALID_NOTES_RAW = {
    "key_concepts": [
        {"id": "c1", "term": "Machine Learning", "definition": "A subset of AI", "importance": "high"},
        {"id": "c2", "term": "Neural Networks", "definition": "Computational models", "importance": "medium"},
    ],
    "sections": [
        {"title": "Introduction", "summary": "Overview of ML.", "bullets": ["ML is important", "AI drives ML"]},
    ],
    "glossary": [
        {"term": "ML", "brief_def": "Machine Learning abbreviation"},
    ],
}

VALID_MCQ_RAW = {
    "questions": [
        {
            "id": "q1",
            "question": "What is machine learning?",
            "options": {"A": "A subset of AI", "B": "A programming language", "C": "A database", "D": "A hardware chip"},
            "correct_answer": "A",
            "explanation": "Machine learning is indeed a subset of artificial intelligence that uses algorithms.",
            "concept_id": "c1",
            "difficulty": "easy",
        }
    ]
}

VALID_FILL_RAW = {
    "questions": [
        {
            "id": "f1",
            "sentence_with_blank": "___ is a subset of artificial intelligence.",
            "answer": "Machine learning",
            "acceptable_variants": ["ML"],
            "hint": "Two-word term starting with M",
            "concept_id": "c1",
        }
    ]
}


# ─────────────────────────────────────────
# Notes validation
# ─────────────────────────────────────────

def test_validate_notes_happy_path():
    notes = validate_notes(VALID_NOTES_RAW, SOURCE_TEXT)
    assert len(notes.key_concepts) == 2
    assert notes.key_concepts[0].id == "c1"
    assert len(notes.sections) == 1
    assert len(notes.glossary) == 1


def test_validate_notes_missing_concepts_raises():
    with pytest.raises(ValidationError):
        validate_notes({"key_concepts": [], "sections": [], "glossary": []}, SOURCE_TEXT)


def test_validate_notes_invalid_importance_coerces():
    raw = {**VALID_NOTES_RAW, "key_concepts": [
        {"id": "c1", "term": "Test", "definition": "Def", "importance": "INVALID"}
    ]}
    notes = validate_notes(raw, SOURCE_TEXT)
    assert notes.key_concepts[0].importance == "medium"


def test_validate_notes_missing_section_creates_fallback():
    raw = {**VALID_NOTES_RAW, "sections": []}
    notes = validate_notes(raw, SOURCE_TEXT)
    assert len(notes.sections) == 1
    assert notes.sections[0].title == "Document Summary"


# ─────────────────────────────────────────
# MCQ validation
# ─────────────────────────────────────────

def test_validate_mcq_happy_path():
    questions = validate_mcq(VALID_MCQ_RAW, {"c1", "c2"}, SOURCE_TEXT)
    assert len(questions) == 1
    assert questions[0].correct_answer == "A"


def test_validate_mcq_invalid_correct_answer_skips():
    raw = {
        "questions": [
            {
                "id": "q1",
                "question": "Bad question?",
                "options": {"A": "O1", "B": "O2", "C": "O3", "D": "O4"},
                "correct_answer": "Z",  # invalid
                "explanation": "explanation",
                "concept_id": "c1",
                "difficulty": "easy",
            }
        ]
    }
    with pytest.raises(ValidationError):
        validate_mcq(raw, {"c1"}, SOURCE_TEXT)


def test_validate_mcq_duplicate_options_skips():
    raw = {
        "questions": [
            {
                "id": "q1",
                "question": "Question?",
                "options": {"A": "Same", "B": "Same", "C": "Different", "D": "Other"},
                "correct_answer": "A",
                "explanation": "explanation",
                "concept_id": "c1",
                "difficulty": "easy",
            }
        ]
    }
    with pytest.raises(ValidationError):
        validate_mcq(raw, {"c1"}, SOURCE_TEXT)


def test_validate_mcq_concept_id_fallback():
    raw = {
        "questions": [
            {**VALID_MCQ_RAW["questions"][0], "concept_id": "nonexistent"}
        ]
    }
    questions = validate_mcq(raw, {"c1", "c2"}, SOURCE_TEXT)
    assert questions[0].concept_id in {"c1", "c2"}


def test_validate_mcq_no_valid_questions_raises():
    with pytest.raises(ValidationError):
        validate_mcq({"questions": []}, {"c1"}, SOURCE_TEXT)


# ─────────────────────────────────────────
# Fill validation
# ─────────────────────────────────────────

def test_validate_fill_happy_path():
    questions = validate_fill(VALID_FILL_RAW, {"c1", "c2"}, SOURCE_TEXT)
    assert len(questions) == 1
    assert questions[0].answer == "Machine learning"


def test_validate_fill_no_blank_skips():
    raw = {
        "questions": [
            {"id": "f1", "sentence_with_blank": "No blank here.", "answer": "Answer", "concept_id": "c1"}
        ]
    }
    with pytest.raises(ValidationError):
        validate_fill(raw, {"c1"}, SOURCE_TEXT)


def test_validate_fill_empty_answer_skips():
    raw = {
        "questions": [
            {"id": "f1", "sentence_with_blank": "___ is here.", "answer": "", "concept_id": "c1"}
        ]
    }
    with pytest.raises(ValidationError):
        validate_fill(raw, {"c1"}, SOURCE_TEXT)
