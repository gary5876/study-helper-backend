"""Unit tests for the prompt builder."""
import pytest
from app.services.prompt_builder import (
    build_notes_prompt,
    build_mcq_prompt,
    build_fill_prompt,
    build_ox_prompt,
    calculate_question_counts,
)

SAMPLE_DOC = "Neural networks are used in deep learning. Gradient descent optimizes the model."
SAMPLE_NOTES = {
    "key_concepts": [{"id": "c1", "term": "Neural Networks"}, {"id": "c2", "term": "Gradient Descent"}],
    "sections": [{"title": "Intro", "summary": "Overview"}],
}


def test_build_notes_prompt_returns_tuple():
    system, user = build_notes_prompt(SAMPLE_DOC)
    assert isinstance(system, str) and len(system) > 10
    assert isinstance(user, str) and len(user) > 10
    assert SAMPLE_DOC in user
    assert "key_concepts" in user


def test_build_mcq_prompt_includes_count():
    system, user = build_mcq_prompt(SAMPLE_DOC, SAMPLE_NOTES, 15)
    assert "15" in user
    assert "Neural Networks" in user
    assert "Gradient Descent" in user


def test_build_mcq_prompt_includes_trap_distractor_guide():
    """Phase A.5: distractor design guide must be in MCQ prompts."""
    _, user = build_mcq_prompt(SAMPLE_DOC, SAMPLE_NOTES, 10)
    assert "DISTRACTOR DESIGN" in user
    assert "sibling-concept confusion" in user


def test_build_fill_prompt_includes_count():
    system, user = build_fill_prompt(SAMPLE_DOC, SAMPLE_NOTES, 10)
    assert "10" in user
    assert "___" in user


def test_build_ox_prompt_basic():
    system, user = build_ox_prompt(SAMPLE_DOC, SAMPLE_NOTES, 8)
    assert isinstance(system, str) and "true/false" in system.lower()
    assert "8" in user
    assert "O|X" in user
    assert "Neural Networks" in user
    # Critical instruction: declarative, not interrogative
    assert "declarative" in user.lower()


def test_build_ox_prompt_includes_balance_directive():
    """OX prompts must instruct ~50/50 O vs X balance."""
    _, user = build_ox_prompt(SAMPLE_DOC, SAMPLE_NOTES, 10)
    assert "50/50" in user or "half" in user.lower()


def test_mcq_prompt_includes_exemplars():
    """Phase A.2: MCQ prompts must include the gold-standard examples block."""
    _, user = build_mcq_prompt(SAMPLE_DOC, SAMPLE_NOTES, 10)
    assert "GOLD STANDARD EXAMPLES" in user
    assert "imitate the *style" in user
    # The first MCQ exemplar's keyword (지도학습) should appear
    assert "지도학습" in user


def test_fill_prompt_includes_exemplars():
    _, user = build_fill_prompt(SAMPLE_DOC, SAMPLE_NOTES, 10)
    assert "GOLD STANDARD EXAMPLES" in user
    assert "역전파" in user  # first FILL exemplar answer


def test_ox_prompt_includes_exemplars():
    _, user = build_ox_prompt(SAMPLE_DOC, SAMPLE_NOTES, 8)
    assert "GOLD STANDARD EXAMPLES" in user
    # First OX exemplar mentions 경사하강법
    assert "경사하강법" in user


def test_exemplars_strip_internal_design_notes():
    """_design_note keys are internal commentary and must NOT leak into prompts."""
    _, user_mcq = build_mcq_prompt(SAMPLE_DOC, SAMPLE_NOTES, 10)
    _, user_fill = build_fill_prompt(SAMPLE_DOC, SAMPLE_NOTES, 10)
    _, user_ox = build_ox_prompt(SAMPLE_DOC, SAMPLE_NOTES, 8)
    for user in (user_mcq, user_fill, user_ox):
        assert "_design_note" not in user
        assert "design_note" not in user


def test_calculate_question_counts_minimum():
    mcq, fill, ox = calculate_question_counts(1)
    assert mcq == 10  # max(10, min(20, 1*3)) = 10
    assert fill == 6  # max(6,  min(15, 1*2)) = 6
    assert ox == 8    # max(8,  min(12, 1*2)) = 8


def test_calculate_question_counts_large():
    mcq, fill, ox = calculate_question_counts(20)
    assert mcq == 20  # capped at 20
    assert fill == 15  # capped at 15
    assert ox == 12   # capped at 12


def test_calculate_question_counts_medium():
    mcq, fill, ox = calculate_question_counts(5)
    assert mcq == 15  # 5*3=15
    assert fill == 10  # 5*2=10
    assert ox == 10   # 5*2=10, within [8,12]
