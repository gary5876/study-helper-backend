"""Quality gate: score generated questions and drop low-quality ones.

Plan §1.3 serving integration. Designed to be called from
`app/routers/generate.py::_run_generation` right after validate_mcq/validate_fill,
but kept dependency-light so it can also run standalone (ml/demo_serving.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ml.features import build_feature_frame
from ml.mlops_config import QUALITY_THRESHOLD


def _as_dict(q: Any) -> dict:
    return q.model_dump() if hasattr(q, "model_dump") else dict(q)


def _good_index(model) -> int:
    classes = list(getattr(model, "classes_", ["bad", "good"]))
    return classes.index("good") if "good" in classes else len(classes) - 1


def score_questions(model, questions: list[Any]) -> list[float]:
    """Return P(good) in [0,1] for each question."""
    if not questions:
        return []
    frame = build_feature_frame([_as_dict(q) for q in questions])
    proba = model.predict_proba(frame)
    gi = _good_index(model)
    return [float(row[gi]) for row in proba]


@dataclass
class GateResult:
    kept: list[Any]
    dropped: list[Any]
    scores: list[float] = field(default_factory=list)
    serving_model: str = "champion"

    @property
    def avg_score(self) -> float:
        return round(sum(self.scores) / len(self.scores), 4) if self.scores else 0.0

    @property
    def dropped_count(self) -> int:
        return len(self.dropped)


def score_and_filter(
    model,
    questions: list[Any],
    serving_model: str = "champion",
    threshold: float = QUALITY_THRESHOLD,
) -> GateResult:
    """Keep questions scoring >= threshold; drop the rest."""
    scores = score_questions(model, questions)
    kept, dropped = [], []
    for q, s in zip(questions, scores):
        (kept if s >= threshold else dropped).append(q)
    return GateResult(kept=kept, dropped=dropped, scores=scores,
                      serving_model=serving_model)
