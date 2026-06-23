"""Generate a deterministic, Postgres-free seed dataset of labeled questions.

Cold-start labels (plan §1.2): well-formed questions -> "good", questions with
known low-quality signatures (duplicate options, empty explanation, missing
answer, too-short stem) -> "bad".

Run:
    python -m ml.build_seed            # base set (with mild label noise)
    python -m ml.build_seed --augment  # base + extra clean examples (retrain demo)

Output: ml/data/seed.jsonl  (one {"q": {...}, "label": "good|bad"} per line)
"""
from __future__ import annotations

import argparse
import json
import os
import random

from ml.mlops_config import DATA_DIR, RANDOM_STATE, SEED_FILE

CONCEPTS = [
    "광합성", "세포 호흡", "운영체제", "교착상태", "정규화", "트랜잭션",
    "수요와 공급", "기회비용", "관성", "엔트로피", "재귀", "해시 테이블",
    "오버피팅", "경사하강법", "분산", "표본 평균", "면역 반응", "삼투압",
]

GOOD_STEMS = [
    "{c}의 핵심 특징으로 가장 적절한 설명은 무엇인가?",
    "{c}에 대한 다음 설명 중 옳은 것은?",
    "{c}이(가) 발생하는 주된 원인으로 가장 알맞은 것은?",
    "{c}의 정의로 가장 정확한 것은?",
]

GOOD_OPTIONS = [
    ["에너지를 화학 결합으로 저장한다", "물질을 무작위로 분해한다",
     "외부 입력 없이 정보를 생성한다", "항상 온도를 낮춘다"],
    ["자원을 효율적으로 배분하는 원리다", "비용을 무한히 증가시킨다",
     "측정이 불가능한 추상 개념이다", "결과가 매번 동일하다"],
    ["상태를 일관되게 유지한다", "데이터를 임의로 삭제한다",
     "순서를 보장하지 않는다", "항상 실패한다"],
]

GOOD_EXPL = [
    "정답은 {c}의 정의에 근거하며, 나머지 보기는 핵심 메커니즘과 무관하다.",
    "{c}의 작동 원리상 정답이 가장 정확하고, 다른 보기는 흔한 오개념이다.",
    "교재 본문에서 {c}을(를) 정답과 같이 설명하므로 옳다.",
]


def _good_mcq(r: random.Random) -> dict:
    c = r.choice(CONCEPTS)
    opts = list(r.choice(GOOD_OPTIONS))
    r.shuffle(opts)
    return {
        "question": r.choice(GOOD_STEMS).format(c=c),
        "options": {"A": opts[0], "B": opts[1], "C": opts[2], "D": opts[3]},
        "correct_answer": "A",
        "explanation": r.choice(GOOD_EXPL).format(c=c),
        "concept_id": "c1",
        "level": r.randint(2, 5),
        "question_type": r.choice(["concept", "application"]),
    }


def _bad_mcq(r: random.Random) -> dict:
    c = r.choice(CONCEPTS)
    flaw = r.randint(0, 2)
    if flaw == 0:  # duplicate / near-identical options
        dup = r.choice(["보기", c, "정답"])
        opts = {"A": dup, "B": dup, "C": dup, "D": dup + " "}
        expl = "정답."
        stem = f"{c}?"
    elif flaw == 1:  # empty explanation + placeholder options
        opts = {"A": "맞음", "B": "맞음", "C": "틀림", "D": "맞음"}
        expl = ""
        stem = "다음 중 옳은 것은?"
    else:  # too-short stem, answer absent from options
        opts = {"A": "기타", "B": "기타", "C": "기타", "D": "기타"}
        expl = ""
        stem = "?"
    return {
        "question": stem,
        "options": opts,
        "correct_answer": "A",
        "explanation": expl,
        "concept_id": "c1",
        "level": r.randint(1, 3),
        "question_type": "concept",
    }


def _good_fill(r: random.Random) -> dict:
    c = r.choice(CONCEPTS)
    return {
        "sentence_with_blank": f"____ 은(는) {c}을(를) 설명하는 핵심 개념이다.",
        "answer": c,
        "acceptable_variants": [c + "현상"],
        "hint": f"{c}와(과) 관련된 용어",
        "concept_id": "c1",
        "level": r.randint(2, 5),
        "question_type": "concept",
    }


def _bad_fill(r: random.Random) -> dict:
    flaw = r.randint(0, 1)
    if flaw == 0:
        return {"sentence_with_blank": "____.", "answer": "", "hint": "",
                "concept_id": "c1", "level": 1, "question_type": "concept"}
    return {"sentence_with_blank": "____ ____ ____ ____", "answer": "정답",
            "hint": "", "concept_id": "c1", "level": 1, "question_type": "concept"}


def build(augment: bool) -> list[dict]:
    r = random.Random(RANDOM_STATE)
    rows: list[dict] = []

    # Base set: 55 good + 55 bad, with ~12% label noise (harder -> leaves room
    # for the augmented retrain to beat the first champion).
    for _ in range(45):
        rows.append({"q": _good_mcq(r), "label": "good"})
    for _ in range(10):
        rows.append({"q": _good_fill(r), "label": "good"})
    for _ in range(45):
        rows.append({"q": _bad_mcq(r), "label": "bad"})
    for _ in range(10):
        rows.append({"q": _bad_fill(r), "label": "bad"})

    noise_n = int(len(rows) * 0.12)
    for idx in r.sample(range(len(rows)), noise_n):
        rows[idx]["label"] = "bad" if rows[idx]["label"] == "good" else "good"

    if augment:
        # Extra clean, noise-free examples -> stronger signal on retrain.
        for _ in range(40):
            rows.append({"q": _good_mcq(r), "label": "good"})
        for _ in range(40):
            rows.append({"q": _bad_mcq(r), "label": "bad"})

    r.shuffle(rows)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--augment", action="store_true",
                        help="append extra clean examples (retrain/swap demo)")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    rows = build(args.augment)
    with open(SEED_FILE, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    n_good = sum(1 for x in rows if x["label"] == "good")
    print(f"[SEED] wrote {len(rows)} rows -> {SEED_FILE}")
    print(f"[SEED] good={n_good} bad={len(rows) - n_good} augment={args.augment}")


if __name__ == "__main__":
    main()
