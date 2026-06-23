"""Feature extraction for the Question Quality Classifier.

Turns a generated question (MCQ or Fill-in-the-blank dict) into one feature
row: a combined text field (for TF-IDF) plus a few engineered numeric signals
that correlate with quality (length, distractor similarity, etc.).

The same function is used at training time (ml/train.py) and serving time
(app/services/quality_gate.py) so train/serve features never drift.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

import pandas as pd

FEATURE_COLUMNS = ["text", "q_len", "exp_len", "opt_dup", "level", "is_mcq"]


def _is_mcq(q: dict[str, Any]) -> bool:
    return "options" in q and isinstance(q.get("options"), dict)


def _option_dup(options: dict[str, Any]) -> float:
    """Max pairwise text similarity among MCQ options (0=distinct, 1=identical).

    High values mean the distractors are too similar to each other / the
    answer — a common low-quality signature.
    """
    vals = [str(v).strip().lower() for v in options.values() if str(v).strip()]
    if len(vals) < 2:
        return 1.0
    worst = 0.0
    for i in range(len(vals)):
        for j in range(i + 1, len(vals)):
            worst = max(worst, SequenceMatcher(None, vals[i], vals[j]).ratio())
    return round(worst, 4)


def featurize_one(q: dict[str, Any]) -> dict[str, Any]:
    """Featurize a single question dict into one feature row."""
    is_mcq = _is_mcq(q)

    if is_mcq:
        stem = str(q.get("question", ""))
        options = q.get("options", {}) or {}
        opt_text = " ".join(str(v) for v in options.values())
        opt_dup = _option_dup(options)
    else:
        stem = str(q.get("sentence_with_blank", q.get("question", "")))
        opt_text = str(q.get("answer", ""))
        opt_dup = 0.0

    explanation = str(q.get("explanation", q.get("hint", "")))
    text = " ".join([stem, opt_text, explanation]).strip()

    try:
        level = int(q.get("level", 3))
    except (TypeError, ValueError):
        level = 3

    return {
        "text": text if text else "(empty)",
        "q_len": len(stem),
        "exp_len": len(explanation),
        "opt_dup": opt_dup,
        "level": max(1, min(5, level)),
        "is_mcq": 1 if is_mcq else 0,
    }


def build_feature_frame(questions: list[dict[str, Any]]) -> pd.DataFrame:
    """Featurize a list of question dicts into a DataFrame with FEATURE_COLUMNS."""
    rows = [featurize_one(q) for q in questions]
    return pd.DataFrame(rows, columns=FEATURE_COLUMNS)
