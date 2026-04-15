"""고아 세션 복구 스크립트.

Phase 4A(count_orphan_sessions.py)로 결과를 본 뒤 실행한다.

수행 동작:
  GROUP A (복구 가능): question_bank.content_json을 user_sessions.result_json에 backfill.
                      status는 'ready' 유지, completed_at이 NULL이면 now()로 채움.
  GROUP B (영구 유실): status='failed', error_message='데이터 유실 — 다시 생성해주세요.'로 전이.
  GROUP B' (pdf_hash NULL): GROUP B와 동일하게 failed 전이.

기본 모드는 dry-run(실제 UPDATE 없이 출력만). `--apply` 플래그를 주면 실제 커밋.

실행 방법:
    cd study-helper-backend
    python -m scripts.heal_orphan_sessions           # dry-run
    python -m scripts.heal_orphan_sessions --apply   # 실제 커밋

환경변수:
    SUPABASE_DB_URL, DATABASE_URL
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 수정한다")
    args = parser.parse_args()

    supabase_url = os.environ.get("SUPABASE_DB_URL")
    database_url = os.environ.get("DATABASE_URL")
    if not supabase_url or not database_url:
        print("ERROR: SUPABASE_DB_URL, DATABASE_URL 둘 다 필요합니다.", file=sys.stderr)
        return 2

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== heal_orphan_sessions [{mode}] ===")

    # 1) 고아 세션 목록 수집
    supa = await asyncpg.connect(supabase_url)
    try:
        orphans = await supa.fetch(
            "SELECT id::text, user_id::text, pdf_hash, pdf_name "
            "FROM user_sessions "
            "WHERE status = 'ready' AND result_json IS NULL"
        )
    finally:
        await supa.close()

    if not orphans:
        print("고아 세션 없음. 종료.")
        return 0
    print(f"고아 세션 총 {len(orphans)}개 발견")

    # 2) question_bank와 조인
    recoverable: dict[str, str] = {}  # pdf_hash -> content_json
    hashes = [o["pdf_hash"] for o in orphans if o["pdf_hash"]]
    if hashes:
        qb = await asyncpg.connect(database_url)
        try:
            qb_rows = await qb.fetch(
                "SELECT pdf_hash, content_json FROM question_bank WHERE pdf_hash = ANY($1::text[])",
                hashes,
            )
            recoverable = {r["pdf_hash"]: r["content_json"] for r in qb_rows}
        finally:
            await qb.close()

    group_a = [o for o in orphans if o["pdf_hash"] and o["pdf_hash"] in recoverable]
    group_b = [o for o in orphans if o["pdf_hash"] and o["pdf_hash"] not in recoverable]
    group_b_nohash = [o for o in orphans if not o["pdf_hash"]]

    print(f"  GROUP A (복구 가능, backfill 예정): {len(group_a)}")
    print(f"  GROUP B (영구 유실, failed 전이):   {len(group_b)}")
    print(f"  GROUP B' (pdf_hash NULL, failed):   {len(group_b_nohash)}")

    if not args.apply:
        print()
        print("dry-run 모드입니다. 실제 반영하려면 --apply 플래그로 재실행하세요.")
        return 0

    # 3) 실제 반영
    supa = await asyncpg.connect(supabase_url)
    try:
        async with supa.transaction():
            # GROUP A backfill
            for o in group_a:
                await supa.execute(
                    "UPDATE user_sessions "
                    "SET result_json = $1, completed_at = COALESCE(completed_at, now()) "
                    "WHERE id = $2::uuid AND result_json IS NULL",
                    recoverable[o["pdf_hash"]], o["id"],
                )
            # GROUP B + B' failed 전이
            for o in [*group_b, *group_b_nohash]:
                await supa.execute(
                    "UPDATE user_sessions "
                    "SET status = 'failed', "
                    "    error_message = '데이터 유실 — 다시 생성해주세요.' "
                    "WHERE id = $1::uuid",
                    o["id"],
                )
    finally:
        await supa.close()

    print()
    print(f"완료: A backfill={len(group_a)}, B failed 전이={len(group_b) + len(group_b_nohash)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
