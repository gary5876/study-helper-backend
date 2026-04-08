"""Unit tests for the prompt builder."""
import pytest
from app.services.prompt_builder import (
    build_notes_prompt,
    build_mcq_prompt,
    build_fill_prompt,
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


def test_build_fill_prompt_includes_count():
    system, user = build_fill_prompt(SAMPLE_DOC, SAMPLE_NOTES, 10)
    assert "10" in user
    assert "___" in user


def test_calculate_question_counts_minimum():
    mcq, fill = calculate_question_counts(1)
    assert mcq == 10  # max(10, min(20, 1*3)) = 10
    assert fill == 6   # max(6,  min(15, 1*2)) = 6


def test_calculate_question_counts_large():
    mcq, fill = calculate_question_counts(20)
    assert mcq == 20  # capped at 20
    assert fill == 15  # capped at 15


def test_calculate_question_counts_medium():
    mcq, fill = calculate_question_counts(5)
    assert mcq == 15  # 5*3=15
    assert fill == 10  # 5*2=10
