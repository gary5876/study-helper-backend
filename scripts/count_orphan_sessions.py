"""Dry-run: 결과 저장이 유실된 "고아 ready 세션" 카운트 / 목록 출력.

실행 방법:
    cd study-helper-backend
    python -m scripts.count_orphan_sessions

환경변수(.env 또는 쉘):
    SUPABASE_DB_URL   user_sessions가 있는 Supabase Postgres
    DATABASE_URL      question_bank가 있는 Railway Postgres

이 스크립트는 **읽기 전용**이다. 아무 것도 수정하지 않는다.

분류:
  - GROUP A (복구 가능):  status='ready' AND result_json IS NULL AND question_bank에 pdf_hash 있음
  - GROUP B (영구 유실):  status='ready' AND result_json IS NULL AND question_bank에 pdf_hash 없음
  - GROUP C (마이그 이전 정상): status='ready' AND result_json IS NOT NULL
  - GROUP D (pending):    status='pending' (self-heal 대상)
  - GROUP E (failed):     status='failed'
"""
from __future__ import annotations

import asyncio
import os
import sys
from collections import defaultdict

import asyncpg


async def main() -> int:
    supabase_url = os.environ.get("SUPABASE_DB_URL")
    database_url = os.environ.get("DATABASE_URL")
    if not supabase_url:
        print("ERROR: SUPABASE_DB_URL 환경변수가 필요합니다.", file=sys.stderr)
        return 2
    if not database_url:
        print("ERROR: DATABASE_URL 환경변수가 필요합니다.", file=sys.stderr)
        return 2

    print("[1/3] Supabase user_sessions 조회...")
    supa = await asyncpg.connect(supabase_url)
    try:
        rows = await supa.fetch(
            "SELECT id::text, user_id::text, pdf_name, pdf_hash, status, "
            "       result_json IS NOT NULL AS has_result, "
            "       created_at "
            "FROM user_sessions "
            "ORDER BY created_at DESC"
        )
    finally:
        await supa.close()

    buckets: dict[str, list] = defaultdict(list)
    ready_null_hashes: set[str] = set()
    for r in rows:
        if r["status"] == "ready" and not r["has_result"]:
            if r["pdf_hash"]:
                buckets["ready_null_result"].append(dict(r))
                ready_null_hashes.add(r["pdf_hash"])
            else:
                buckets["ready_null_result_no_hash"].append(dict(r))
        elif r["status"] == "ready" and r["has_result"]:
            buckets["ready_ok"].append(dict(r))
        elif r["status"] == "pending":
            buckets["pending"].append(dict(r))
        elif r["status"] == "failed":
            buckets["failed"].append(dict(r))
        else:
            buckets[f"other_{r['status']}"].append(dict(r))

    print(f"[2/3] question_bank에서 ready_null_result의 pdf_hash 교집합 확인...")
    recoverable: set[str] = set()
    if ready_null_hashes:
        qb = await asyncpg.connect(database_url)
        try:
            qb_rows = await qb.fetch(
                "SELECT pdf_hash FROM question_bank WHERE pdf_hash = ANY($1::text[])",
                list(ready_null_hashes),
            )
            recoverable = {r["pdf_hash"] for r in qb_rows}
        finally:
            await qb.close()

    print("[3/3] 결과 집계")
    print("=" * 72)
    total = len(rows)
    print(f"총 user_sessions 행 수: {total}")
    print()
    print(f"  GROUP A (복구 가능 — result_json 백필 대상)          : ", end="")
    group_a = [r for r in buckets["ready_null_result"] if r["pdf_hash"] in recoverable]
    print(f"{len(group_a)}")
    print(f"  GROUP B (영구 유실 — question_bank에도 없음)         : ", end="")
    group_b = [r for r in buckets["ready_null_result"] if r["pdf_hash"] not in recoverable]
    print(f"{len(group_b)}")
    print(f"  GROUP B' (ready이나 pdf_hash 자체가 NULL — 완전 유실): ", end="")
    print(f"{len(buckets['ready_null_result_no_hash'])}")
    print(f"  GROUP C (기존 정상 — result_json 있음)               : ", end="")
    print(f"{len(buckets['ready_ok'])}")
    print(f"  GROUP D (pending — self-heal 대상)                   : ", end="")
    print(f"{len(buckets['pending'])}")
    print(f"  GROUP E (failed)                                     : ", end="")
    print(f"{len(buckets['failed'])}")
    print("=" * 72)

    if group_a:
        print()
        print("GROUP A 샘플 (상위 5개):")
        for r in group_a[:5]:
            print(f"  {r['id']}  {r['pdf_name']!r}  hash={r['pdf_hash'][:12] if r['pdf_hash'] else '-'}  {r['created_at']}")
    if group_b:
        print()
        print("GROUP B 샘플 (상위 5개 — 재생성 유도 예정):")
        for r in group_b[:5]:
            print(f"  {r['id']}  {r['pdf_name']!r}  hash={r['pdf_hash'][:12] if r['pdf_hash'] else '-'}  {r['created_at']}")

    print()
    print("이 스크립트는 read-only 입니다. 복구는 heal_orphan_sessions.py를 실행하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
