# Supabase 마이그레이션

Supabase SQL Editor에서 실행한 쿼리를 순번대로 기록합니다.

## 파일 명명 규칙

```
NNN_설명.sql
```

- `NNN`: 3자리 순번 (001, 002, ...)
- 설명: 영문 snake_case

## 실행 방법

Supabase 대시보드 → SQL Editor에서 해당 파일 내용을 복붙 후 실행합니다.

## 적용 이력

| 순번 | 파일 | 설명 | 적용일 |
|------|------|------|--------|
| 001 | `001_initial_schema.sql` | 초기 테이블 4개 + RLS 정책 | 적용완료 |
| 002 | `002_review_schedule_unique_constraint.sql` | review_schedule UNIQUE 키에 user_id 추가 | 적용완료 |
| 003 | `003_sessions_unique_constraint.sql` | user_sessions에 (user_id, pdf_hash) UNIQUE 추가 | 적용완료 |
