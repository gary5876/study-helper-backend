"""Gold-standard question exemplars used as few-shot examples in prompts.

Phase A.2: the prompts previously contained only directives and no examples,
so the LLM had no anchor for what a *good* question looks like. These
exemplars demonstrate the patterns we want — trap distractors, subtle false
statements, application-level fills — without dictating subject matter.

Each prompt is instructed to **imitate the style, not copy the content**.
The exemplars deliberately span domains (ML / programming / general) so the
model does not bias toward any single subject.
"""
from __future__ import annotations

import json


# ─────────────────────────────────────────
# MCQ exemplars — emphasise trap distractors and level-4+ reasoning
# ─────────────────────────────────────────
MCQ_EXEMPLARS = [
    {
        "question": "지도학습(supervised learning)과 비지도학습(unsupervised learning)의 본질적 차이로 가장 적절한 것은?",
        "options": {
            "A": "사용하는 알고리즘의 복잡도가 다르다",
            "B": "학습 데이터에 레이블(label)이 부여되어 있는지 여부",
            "C": "신경망의 층 수가 다르다",
            "D": "GPU 사용 여부의 차이",
        },
        "correct_answer": "B",
        "explanation": (
            "지도학습은 입력-정답 쌍으로 구성된 레이블된 데이터로 학습하고, "
            "비지도학습은 레이블 없이 데이터의 구조·군집을 발견한다. "
            "알고리즘 복잡도·층 수·하드웨어는 두 패러다임을 구분하는 본질이 아니다."
        ),
        "level": 3,
        "question_type": "concept",
        "_design_note": "Sibling-concept confusion trap. Each distractor is a real ML topic but unrelated to the actual distinction.",
    },
    {
        "question": "다음 의사코드의 동작으로 옳은 것은?\n\n    let x = 10\n    let y = x\n    y = y + 5\n    print(x, y)",
        "options": {
            "A": "10, 15 — 값 복사가 일어났기 때문",
            "B": "15, 15 — 같은 변수를 가리키므로",
            "C": "컴파일 오류 — let은 재할당 불가",
            "D": "10, 10 — y의 변경은 무시된다",
        },
        "correct_answer": "A",
        "explanation": (
            "primitive 값 대입은 복사를 일으키며, y의 변경은 x에 영향을 주지 않는다. "
            "B는 참조 시맨틱과 혼동, C는 const/val과 혼동, D는 대입 자체를 부정하는 함정."
        ),
        "level": 4,
        "question_type": "application",
        "_design_note": "Code-output prediction. Distractors map to specific misconceptions (reference vs value, const vs let, etc.).",
    },
]


# ─────────────────────────────────────────
# OX exemplars — declarative, with a *specific* error in the false case
# ─────────────────────────────────────────
OX_EXEMPLARS = [
    {
        "statement": "경사하강법은 손실 함수의 모든 형태에 대해 항상 전역 최소점에 수렴한다.",
        "answer": "X",
        "explanation": (
            "오류 위치: '전역 최소점'. 경사하강법은 일반적으로 지역 최소점(local minimum) "
            "수렴만 보장하며, 비볼록(non-convex) 손실에서는 전역 최소점 수렴이 보장되지 않는다."
        ),
        "level": 4,
        "question_type": "concept",
        "_design_note": "False statement with a single specific clause to refute, not vague.",
    },
    {
        "statement": "역전파(backpropagation) 알고리즘은 연쇄 법칙(chain rule)을 이용해 손실 함수의 그래디언트를 계산한다.",
        "answer": "O",
        "explanation": "체인 룰을 출력층에서 입력층으로 역방향 전파하여 각 파라미터의 그래디언트를 구한다.",
        "level": 2,
        "question_type": "concept",
        "_design_note": "Truthful but requires understanding of *why*, not pure recall.",
    },
]


# ─────────────────────────────────────────
# FILL exemplars — the blank must be a load-bearing technical term
# ─────────────────────────────────────────
FILL_EXEMPLARS = [
    {
        "sentence_with_blank": "신경망의 학습 과정에서 ___ 알고리즘은 손실 함수의 그래디언트를 출력층에서 입력층으로 전파한다.",
        "answer": "역전파",
        "acceptable_variants": ["backpropagation", "백프로퍼게이션"],
        "hint": "체인 룰을 응용한 그래디언트 전파 기법",
        "level": 3,
        "question_type": "concept",
        "_design_note": "Blank is the entire technical term, not a filler word.",
    },
    {
        "sentence_with_blank": "데이터셋이 불균형할 때 단순 정확도(accuracy)보다 ___ 또는 F1 점수가 더 신뢰할 수 있는 평가 지표로 사용된다.",
        "answer": "정밀도",
        "acceptable_variants": ["precision", "recall", "재현율"],
        "hint": "양성 예측 중 실제 양성의 비율을 의미하는 지표",
        "level": 4,
        "question_type": "application",
        "_design_note": "Application-level: the blank requires understanding *why* accuracy is misleading.",
    },
]


def _strip_design_note(d: dict) -> dict:
    """Remove the internal _design_note key before serialisation."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def mcq_exemplars_block() -> str:
    """Return a JSON-formatted few-shot block for MCQ prompts."""
    serialisable = [_strip_design_note(e) for e in MCQ_EXEMPLARS]
    return json.dumps(serialisable, ensure_ascii=False, indent=2)


def ox_exemplars_block() -> str:
    serialisable = [_strip_design_note(e) for e in OX_EXEMPLARS]
    return json.dumps(serialisable, ensure_ascii=False, indent=2)


def fill_exemplars_block() -> str:
    serialisable = [_strip_design_note(e) for e in FILL_EXEMPLARS]
    return json.dumps(serialisable, ensure_ascii=False, indent=2)
