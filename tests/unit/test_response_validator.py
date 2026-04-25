"""Unit tests for the response validator."""
import pytest

from app.core.exceptions import ValidationError
from app.services.response_validator import (
    _tokenize,
    validate_notes,
    validate_mcq,
    validate_fill,
    validate_ox,
)


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


# ─────────────────────────────────────────
# Tokenizer — Korean + English support
# ─────────────────────────────────────────

def test_tokenize_extracts_korean():
    tokens = _tokenize("코틀린은 자바와 100% 호환된다")
    assert "코틀린은" in tokens
    assert "자바와" in tokens
    assert "호환된다" in tokens


def test_tokenize_skips_single_korean_char():
    tokens = _tokenize("가 나 다 코틀린")
    # Single Korean characters are noise; only 2+ char tokens kept
    assert "가" not in tokens
    assert "코틀린" in tokens


def test_tokenize_extracts_mixed_korean_english():
    tokens = _tokenize("Kotlin은 JVM 기반 언어이다")
    assert "kotlin" in tokens  # lowercased
    assert "jvm" in tokens
    assert "기반" in tokens
    assert "언어이다" in tokens


def test_tokenize_legacy_english_still_works():
    tokens = _tokenize("Machine Learning is a subset of AI.")
    assert "machine" in tokens
    assert "learning" in tokens
    assert "subset" in tokens
    # 2-letter English words still excluded (threshold = 3+)
    assert "ai" not in tokens
    assert "is" not in tokens


# ─────────────────────────────────────────
# OX validation
# ─────────────────────────────────────────

VALID_OX_RAW = {
    "questions": [
        {
            "id": "ox1",
            "statement": "Neural networks are computational models inspired by the brain.",
            "answer": "O",
            "explanation": "The document explicitly states this — neural networks model the brain's computation.",
            "concept_id": "c1",
            "level": 2,
            "question_type": "concept",
        },
        {
            "id": "ox2",
            "statement": "Gradient descent always finds the global minimum of any loss surface.",
            "answer": "X",
            "explanation": "Gradient descent only guarantees convergence to a local minimum, not global.",
            "concept_id": "c2",
            "level": 4,
            "question_type": "concept",
        },
    ]
}


def test_validate_ox_happy_path():
    qs = validate_ox(VALID_OX_RAW, {"c1", "c2"}, SOURCE_TEXT)
    assert len(qs) == 2
    assert qs[0].answer == "O"
    assert qs[1].answer == "X"
    assert qs[1].level == 4


def test_validate_ox_normalizes_true_false_strings():
    raw = {
        "questions": [
            {**VALID_OX_RAW["questions"][0], "answer": "True"},
            {**VALID_OX_RAW["questions"][1], "answer": "false"},
        ]
    }
    qs = validate_ox(raw, {"c1", "c2"}, SOURCE_TEXT)
    assert qs[0].answer == "O"
    assert qs[1].answer == "X"


def test_validate_ox_normalizes_korean_truthy_words():
    raw = {
        "questions": [
            {**VALID_OX_RAW["questions"][0], "answer": "참"},
            {**VALID_OX_RAW["questions"][1], "answer": "거짓"},
        ]
    }
    qs = validate_ox(raw, {"c1", "c2"}, SOURCE_TEXT)
    assert qs[0].answer == "O"
    assert qs[1].answer == "X"


def test_validate_ox_strips_trailing_question_mark():
    raw = {
        "questions": [
            {**VALID_OX_RAW["questions"][0], "statement": "Neural networks are inspired by the brain?"},
        ]
    }
    qs = validate_ox(raw, {"c1"}, SOURCE_TEXT)
    assert not qs[0].statement.endswith("?")


def test_validate_ox_skips_invalid_answer():
    raw = {
        "questions": [
            {**VALID_OX_RAW["questions"][0]},  # valid
            {**VALID_OX_RAW["questions"][1], "answer": "maybe"},  # invalid
        ]
    }
    qs = validate_ox(raw, {"c1", "c2"}, SOURCE_TEXT)
    assert len(qs) == 1
    assert qs[0].id == "ox1"


def test_validate_ox_no_valid_raises():
    with pytest.raises(ValidationError):
        validate_ox({"questions": []}, {"c1"}, SOURCE_TEXT)


def test_validate_ox_concept_id_fallback():
    raw = {"questions": [{**VALID_OX_RAW["questions"][0], "concept_id": "nonexistent"}]}
    qs = validate_ox(raw, {"c1", "c2"}, SOURCE_TEXT)
    assert qs[0].concept_id in {"c1", "c2"}


def test_validate_mcq_korean_duplicate_detection():
    """Two near-identical Korean questions should trigger duplicate filter
    (broken pre-fix because the tokenizer dropped all Korean tokens)."""
    raw = {
        "questions": [
            {
                "id": "q1",
                "question": "코틀린에서 val과 var의 차이점은 무엇입니까?",
                "options": {"A": "재할당 가능", "B": "재할당 불가", "C": "타입 추론", "D": "널 안전성"},
                "correct_answer": "B",
                "explanation": "val은 재할당이 불가능하고 var는 재할당이 가능하다.",
                "concept_id": "c1",
                "level": 3,
                "question_type": "concept",
            },
            {
                "id": "q2",
                "question": "코틀린에서 val과 var의 차이점은 무엇인가요?",  # near-duplicate
                "options": {"A": "다른 옵션", "B": "재할당 불가", "C": "타입", "D": "널"},
                "correct_answer": "B",
                "explanation": "val은 재할당이 불가능하다.",
                "concept_id": "c1",
                "level": 3,
                "question_type": "concept",
            },
        ]
    }
    qs = validate_mcq(raw, {"c1"}, "코틀린에서 val과 var는 재할당 가능 여부가 다르다.")
    # Duplicate filter should keep only the first
    assert len(qs) == 1
    assert qs[0].id == "q1"
