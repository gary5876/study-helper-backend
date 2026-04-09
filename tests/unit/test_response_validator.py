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
            "level": 3,
            "question_type": "concept",
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
            "level": 3,
            "question_type": "concept",
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
# MCQ validation — core
# ─────────────────────────────────────────

def test_validate_mcq_happy_path():
    questions = validate_mcq(VALID_MCQ_RAW, {"c1", "c2"}, SOURCE_TEXT)
    assert len(questions) == 1
    assert questions[0].correct_answer == "A"
    assert questions[0].level == 3
    assert questions[0].question_type == "concept"


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
                "level": 3,
                "question_type": "concept",
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
                "level": 3,
                "question_type": "concept",
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
# MCQ validation — level coercion
# ─────────────────────────────────────────

def test_validate_mcq_level_integer_preserved():
    for level in (1, 2, 3, 4, 5):
        raw = {"questions": [{**VALID_MCQ_RAW["questions"][0], "level": level}]}
        qs = validate_mcq(raw, {"c1"}, SOURCE_TEXT)
        assert qs[0].level == level


def test_validate_mcq_level_out_of_range_clamped():
    raw = {"questions": [{**VALID_MCQ_RAW["questions"][0], "level": 9}]}
    qs = validate_mcq(raw, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 5

    raw = {"questions": [{**VALID_MCQ_RAW["questions"][0], "level": 0}]}
    qs = validate_mcq(raw, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 1


def test_validate_mcq_level_missing_defaults_to_3():
    q = {k: v for k, v in VALID_MCQ_RAW["questions"][0].items() if k != "level"}
    raw = {"questions": [q]}
    qs = validate_mcq(raw, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 3


# ─────────────────────────────────────────
# MCQ validation — backward compat (legacy difficulty strings)
# ─────────────────────────────────────────

def test_validate_mcq_legacy_difficulty_easy():
    q = {k: v for k, v in VALID_MCQ_RAW["questions"][0].items() if k != "level"}
    q["difficulty"] = "easy"
    qs = validate_mcq({"questions": [q]}, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 2


def test_validate_mcq_legacy_difficulty_medium():
    q = {k: v for k, v in VALID_MCQ_RAW["questions"][0].items() if k != "level"}
    q["difficulty"] = "medium"
    qs = validate_mcq({"questions": [q]}, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 3


def test_validate_mcq_legacy_difficulty_hard():
    q = {k: v for k, v in VALID_MCQ_RAW["questions"][0].items() if k != "level"}
    q["difficulty"] = "hard"
    qs = validate_mcq({"questions": [q]}, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 4


def test_validate_mcq_level_takes_priority_over_difficulty():
    """If both level and difficulty are present, level wins."""
    q = {**VALID_MCQ_RAW["questions"][0], "level": 5, "difficulty": "easy"}
    qs = validate_mcq({"questions": [q]}, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 5


# ─────────────────────────────────────────
# MCQ validation — question_type
# ─────────────────────────────────────────

def test_validate_mcq_question_type_concept():
    raw = {"questions": [{**VALID_MCQ_RAW["questions"][0], "question_type": "concept"}]}
    qs = validate_mcq(raw, {"c1"}, SOURCE_TEXT)
    assert qs[0].question_type == "concept"


def test_validate_mcq_question_type_application():
    raw = {"questions": [{**VALID_MCQ_RAW["questions"][0], "question_type": "application"}]}
    qs = validate_mcq(raw, {"c1"}, SOURCE_TEXT)
    assert qs[0].question_type == "application"


def test_validate_mcq_question_type_invalid_defaults_to_concept():
    raw = {"questions": [{**VALID_MCQ_RAW["questions"][0], "question_type": "unknown"}]}
    qs = validate_mcq(raw, {"c1"}, SOURCE_TEXT)
    assert qs[0].question_type == "concept"


def test_validate_mcq_question_type_missing_defaults_to_concept():
    q = {k: v for k, v in VALID_MCQ_RAW["questions"][0].items() if k != "question_type"}
    qs = validate_mcq({"questions": [q]}, {"c1"}, SOURCE_TEXT)
    assert qs[0].question_type == "concept"


# ─────────────────────────────────────────
# Fill validation — core
# ─────────────────────────────────────────

def test_validate_fill_happy_path():
    questions = validate_fill(VALID_FILL_RAW, {"c1", "c2"}, SOURCE_TEXT)
    assert len(questions) == 1
    assert questions[0].answer == "Machine learning"
    assert questions[0].level == 3
    assert questions[0].question_type == "concept"


def test_validate_fill_no_blank_skips():
    raw = {
        "questions": [
            {"id": "f1", "sentence_with_blank": "No blank here.", "answer": "Answer", "concept_id": "c1",
             "level": 3, "question_type": "concept"}
        ]
    }
    with pytest.raises(ValidationError):
        validate_fill(raw, {"c1"}, SOURCE_TEXT)


def test_validate_fill_empty_answer_skips():
    raw = {
        "questions": [
            {"id": "f1", "sentence_with_blank": "___ is here.", "answer": "", "concept_id": "c1",
             "level": 3, "question_type": "concept"}
        ]
    }
    with pytest.raises(ValidationError):
        validate_fill(raw, {"c1"}, SOURCE_TEXT)


# ─────────────────────────────────────────
# Fill validation — level & question_type
# ─────────────────────────────────────────

def test_validate_fill_level_preserved():
    raw = {"questions": [{**VALID_FILL_RAW["questions"][0], "level": 5}]}
    qs = validate_fill(raw, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 5


def test_validate_fill_legacy_difficulty_hard():
    q = {k: v for k, v in VALID_FILL_RAW["questions"][0].items() if k != "level"}
    q["difficulty"] = "hard"
    qs = validate_fill({"questions": [q]}, {"c1"}, SOURCE_TEXT)
    assert qs[0].level == 4


def test_validate_fill_question_type_application():
    raw = {"questions": [{**VALID_FILL_RAW["questions"][0], "question_type": "application"}]}
    qs = validate_fill(raw, {"c1"}, SOURCE_TEXT)
    assert qs[0].question_type == "application"
