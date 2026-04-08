"""Unit tests for shared JSON extraction utilities (app/services/json_utils.py)."""
from __future__ import annotations

import json
import pytest

from app.core.exceptions import GenerationError
from app.services.json_utils import extract_json, recover_partial_questions


# ─────────────────────────────────────────
# extract_json — clean input
# ─────────────────────────────────────────

def test_extract_json_plain_object():
    raw = '{"key": "value", "num": 42}'
    result = extract_json(raw)
    assert result == {"key": "value", "num": 42}


def test_extract_json_with_json_fence():
    raw = '```json\n{"questions": []}\n```'
    result = extract_json(raw)
    assert result == {"questions": []}


def test_extract_json_with_plain_fence():
    raw = '```\n{"ok": true}\n```'
    result = extract_json(raw)
    assert result == {"ok": True}


def test_extract_json_strips_leading_whitespace():
    raw = '   \n{"a": 1}\n   '
    result = extract_json(raw)
    assert result["a"] == 1


# ─────────────────────────────────────────
# extract_json — recovery path
# ─────────────────────────────────────────

def test_extract_json_truncated_recovers_questions():
    # Valid JSON objects inside a truncated array
    partial = '{"questions": [{"id": "q1", "question": "What?", "answer": "A"}, {"id": "q2", "question": "Why?", "answer": "B"}'
    result = extract_json(partial)
    assert "questions" in result
    assert len(result["questions"]) == 2
    assert result["questions"][0]["id"] == "q1"


def test_extract_json_invalid_raises_generation_error():
    with pytest.raises(GenerationError):
        extract_json("this is not json at all")


def test_extract_json_empty_raises_generation_error():
    with pytest.raises(GenerationError):
        extract_json("")


# ─────────────────────────────────────────
# recover_partial_questions
# ─────────────────────────────────────────

def test_recover_no_questions_key_returns_none():
    result = recover_partial_questions('{"other": [{"a": 1}]}')
    assert result is None


def test_recover_empty_array_returns_none():
    # "[" found but no complete "{}" objects
    result = recover_partial_questions('{"questions": [not-an-object')
    assert result is None


def test_recover_one_valid_object():
    text = '{"questions": [{"id": "q1", "text": "Hello"},'
    result = recover_partial_questions(text)
    assert result is not None
    assert len(result["questions"]) == 1
    assert result["questions"][0]["id"] == "q1"


def test_recover_skips_malformed_objects():
    # Mix of valid and invalid JSON objects
    text = '{"questions": [{"id": "q1"}, {broken json}, {"id": "q3"}]}'
    result = recover_partial_questions(text)
    assert result is not None
    # q1 and q3 are valid; the broken one is skipped
    ids = [q["id"] for q in result["questions"]]
    assert "q1" in ids
    assert "q3" in ids
