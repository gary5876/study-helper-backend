"""Shared JSON extraction utilities used by both Anthropic and Gemini clients."""
from __future__ import annotations

import json
import logging

from app.core.exceptions import GenerationError

logger = logging.getLogger(__name__)


def recover_partial_questions(text: str) -> dict | None:
    """Extract complete question objects from truncated JSON via brace counting."""
    array_start = text.find("[", text.find('"questions"'))
    if array_start == -1:
        return None
    questions = []
    depth = 0
    obj_start = None
    for i, c in enumerate(text[array_start:], start=array_start):
        if c == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    questions.append(json.loads(text[obj_start : i + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = None
    if questions:
        logger.warning("Recovered %d complete questions from truncated JSON", len(questions))
        return {"questions": questions}
    return None


def extract_json(raw: str) -> dict:
    """Strip markdown fences if present and parse JSON.
    Falls back to partial recovery for truncated responses."""
    text = raw.strip()
    # Remove ```json ... ``` fences
    if text.startswith("```"):
        lines = text.split("\n")
        start = 1 if lines[0].startswith("```") else 0
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[start:end])
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        recovered = recover_partial_questions(text)
        if recovered:
            return recovered
        raise GenerationError(
            "서비스에 일시적인 문제가 발생했습니다. 잠시 후 다시 시도해주세요.",
            status_code=500,
        )
