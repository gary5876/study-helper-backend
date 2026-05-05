# 변경 요약

<!-- 무엇을 왜 바꿨는지 1~3줄로 -->

## 관련 이슈

<!-- closes #123, refs #456 / 없으면 "없음" -->

## 변경 유형

- [ ] feat — 새 기능
- [ ] fix — 버그 수정
- [ ] security — 보안 수정
- [ ] refactor — 동작 변경 없는 리팩터
- [ ] perf — 성능 개선
- [ ] docs — 문서만
- [ ] test — 테스트만
- [ ] chore — 설정·의존성·CI

## 영향 영역

- [ ] `app/routers/` — API 엔드포인트
- [ ] `app/services/` — 도메인 서비스 (LLM·PDF·세션·검증)
- [ ] `app/core/` — 인증·설정·예외
- [ ] `app/models/` — Pydantic 스키마
- [ ] `migrations/` — DB 스키마
- [ ] `tests/` — pytest

## 테스트

- [ ] `pytest tests/ -v` 통과
- [ ] 신규 단위/통합 테스트 추가
- [ ] 한글 케이스 포함 (해당 시)
- [ ] 로컬 `docker-compose up`으로 실제 호출 검증

## 보안 / 호환성 체크

- [ ] 인증·세션 소유권 검증 우회 없음
- [ ] 민감 정보 (API 키·토큰·세션 ID·스택트레이스) 응답·로그 노출 없음
- [ ] Pydantic 입력 검증 누락 없음
- [ ] DB 마이그레이션 idempotent + rollback 가능
- [ ] LLM 비용 영향 검토 (토큰 한도·재시도·타임아웃)
- [ ] 기존 클라이언트(모바일·웹) 호환

## 비고

<!-- 리뷰어가 알아야 할 트레이드오프, 후속 작업, 운영 변경 등 -->
