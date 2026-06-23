"""Fast, deterministic tests for the quality model serving path.

No MLflow training / DB needed: features are pure, and the gate is tested with
a stub model so the suite stays fast.
"""
import numpy as np

from app.services.quality_gate import score_and_filter, score_questions
from ml.features import build_feature_frame, featurize_one

GOOD = {
    "question": "광합성의 핵심 특징으로 가장 적절한 설명은 무엇인가?",
    "options": {"A": "에너지를 화학 결합으로 저장한다", "B": "물질을 무작위로 분해한다",
                "C": "외부 입력 없이 정보를 생성한다", "D": "항상 온도를 낮춘다"},
    "explanation": "정답은 광합성의 정의에 근거한다.",
    "level": 4,
}
BAD = {
    "question": "?",
    "options": {"A": "기타", "B": "기타", "C": "기타", "D": "기타"},
    "explanation": "",
    "level": 1,
}


class StubModel:
    """Predicts 'good' when explanation length > 0, else 'bad'."""
    classes_ = np.array(["bad", "good"])

    def predict_proba(self, frame):
        out = []
        for exp_len in frame["exp_len"]:
            out.append([0.05, 0.95] if exp_len > 0 else [0.95, 0.05])
        return np.array(out)


def test_featurize_distinguishes_good_and_bad():
    g = featurize_one(GOOD)
    b = featurize_one(BAD)
    assert g["exp_len"] > 0 and b["exp_len"] == 0
    assert b["opt_dup"] > g["opt_dup"]      # bad has near-identical options
    assert g["is_mcq"] == 1 and b["is_mcq"] == 1


def test_feature_frame_columns():
    frame = build_feature_frame([GOOD, BAD])
    assert list(frame.columns) == ["text", "q_len", "exp_len", "opt_dup",
                                   "level", "is_mcq"]
    assert len(frame) == 2


def test_score_questions_with_stub():
    scores = score_questions(StubModel(), [GOOD, BAD])
    assert scores[0] > 0.9 and scores[1] < 0.1


def test_score_and_filter_drops_low_quality():
    result = score_and_filter(StubModel(), [GOOD, BAD], threshold=0.5)
    assert result.kept == [GOOD]
    assert result.dropped == [BAD]
    assert result.dropped_count == 1
    assert 0.0 <= result.avg_score <= 1.0


def test_gate_handles_empty():
    result = score_and_filter(StubModel(), [], threshold=0.5)
    assert result.kept == [] and result.dropped == []
    assert result.avg_score == 0.0
