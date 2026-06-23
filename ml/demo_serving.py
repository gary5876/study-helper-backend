"""Standalone demo of the quality gate + canary serving (no DB / no LLM).

Run: python -m ml.demo_serving
"""
from collections import Counter

from app.services.model_loader import load_champion, select_serving_model
from app.services.quality_gate import score_and_filter

GOOD = {
    "question": "광합성의 핵심 특징으로 가장 적절한 설명은 무엇인가?",
    "options": {"A": "에너지를 화학 결합으로 저장한다",
                "B": "물질을 무작위로 분해한다",
                "C": "외부 입력 없이 정보를 생성한다",
                "D": "항상 온도를 낮춘다"},
    "correct_answer": "A",
    "explanation": "정답은 광합성의 정의에 근거하며, 나머지 보기는 핵심 메커니즘과 무관하다.",
    "concept_id": "c1", "level": 4, "question_type": "concept",
}
BAD = {
    "question": "?",
    "options": {"A": "기타", "B": "기타", "C": "기타", "D": "기타"},
    "correct_answer": "A", "explanation": "",
    "concept_id": "c1", "level": 1, "question_type": "concept",
}


def main() -> None:
    print("=== 1) Quality gate (champion) ===")
    model = load_champion()
    result = score_and_filter(model, [GOOD, BAD], serving_model="champion")
    for q, s in zip([GOOD, BAD], result.scores):
        verdict = "KEEP" if s >= 0.5 else "DROP"
        print(f"  P(good)={s:.3f} [{verdict}] {q['question'][:40]!r}")
    print(f"  kept={len(result.kept)} dropped={result.dropped_count} "
          f"avg_score={result.avg_score}")

    print("\n=== 2) Canary routing (100 requests) ===")
    counts = Counter(select_serving_model()[1] for _ in range(100))
    print(f"  {dict(counts)}")


if __name__ == "__main__":
    main()
