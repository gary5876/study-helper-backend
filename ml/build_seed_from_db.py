"""Build a seed dataset from the REAL question_bank (Railway dev DB).

Read-only: runs a single SELECT on question_bank, extracts the generated MCQ /
fill questions as "good" examples, and synthesizes balanced "bad" examples.
The result replaces ml/data/seed.jsonl, so `python -m ml.train` then trains on
real data.

Credentials: never passed on the command line. Provide the connection string
either as the DATABASE_URL environment variable, or in study-helper-backend/.env
as a line `DATABASE_URL=postgresql://...` (this script reads it; the assistant
never sees the value).

Run:
    python -m ml.build_seed_from_db
    python -m ml.train
"""
from __future__ import annotations

import asyncio
import json
import os
import random

from ml.build_seed import _bad_fill, _bad_mcq
from ml.mlops_config import BACKEND_DIR, DATA_DIR, RANDOM_STATE, SEED_FILE


def _load_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    env_path = os.path.join(BACKEND_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DATABASE_URL="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(
        "DATABASE_URL not found. Set the env var or add it to "
        "study-helper-backend/.env"
    )


async def _fetch_content_jsons(database_url: str) -> list[str]:
    import asyncpg  # imported here so the rest of ml/ has no hard dependency

    conn = await asyncpg.connect(database_url)
    try:
        rows = await conn.fetch(
            "SELECT content_json FROM question_bank ORDER BY created_at"
        )
        return [r["content_json"] for r in rows]
    finally:
        await conn.close()


def _extract_questions(content_jsons: list[str]) -> list[dict]:
    questions: list[dict] = []
    for cj in content_jsons:
        try:
            content = json.loads(cj)
        except Exception:
            continue
        questions.extend(content.get("mcq_questions", []) or [])
        questions.extend(content.get("fill_questions", []) or [])
    return questions


def main() -> None:
    database_url = _load_database_url()
    content_jsons = asyncio.run(_fetch_content_jsons(database_url))
    good_qs = _extract_questions(content_jsons)

    print(f"[DB-SEED] banks={len(content_jsons)} real_questions={len(good_qs)}")
    if not good_qs:
        raise SystemExit("question_bank has no questions yet — nothing to seed.")

    r = random.Random(RANDOM_STATE)
    rows = [{"q": q, "label": "good"} for q in good_qs]

    # Balanced synthetic negatives (no real 'bad' labels exist yet — cold start).
    n_bad = len(good_qs)
    for i in range(n_bad):
        bad = _bad_fill(r) if i % 4 == 0 else _bad_mcq(r)
        rows.append({"q": bad, "label": "bad"})

    r.shuffle(rows)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SEED_FILE, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"[DB-SEED] wrote {len(rows)} rows "
          f"(good={len(good_qs)} bad={n_bad}) -> {SEED_FILE}")


if __name__ == "__main__":
    main()
