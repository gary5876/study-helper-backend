# Study Helper — Backend API

FastAPI 백엔드. PDF를 받아 AI(Anthropic Claude / OpenAI GPT / TimelyGPT)로 학습 콘텐츠(노트 · MCQ · 빈칸 채우기)를 생성합니다.

---

## 현재 상태 (2026-05-04)

> **2026-05-04** — main 브랜치 AWS 배포 인프라 도입. EC2 (`study-helper-backend`,
> EIP `54.116.95.144`) + ECR + SSM Parameter Store + GitHub Actions OIDC.
> 시크릿은 GH Secrets → SSM Parameter Store (`/study-helper/backend/*`,
> SecureString) → `deploy.sh` 가 부팅 시 fetch 해서 `.env.prod` 생성. 워크플로우
> 는 `test → security → docker-build (PR) → build-and-push → sync-secrets →
> deploy` 6 jobs 구조. 상세: `deploy/`.

### 완성된 기능

- [x] PDF 업로드 + pdfplumber 파싱 (최대 50페이지 / 20MB)
- [x] **유료 플랜 (paid)** — Anthropic Claude Sonnet (서킷 브레이커 + 지수 백오프 재시도)
- [x] **GPT 플랜 (gpt)** — OpenAI GPT-4o-mini / GPT-4o 등 (서킷 브레이커 + 재시도, JSON mode)
- [x] **TimelyGPT 플랜 (timely)** — TimelyGPT (timelygpt.co.kr) API 키로 50+ 모델 선택 (토큰 캐싱 55분 + 서킷 브레이커 + 재시도)
- [x] **모델 선택** — `GenerateOptions.model` 필드로 클라이언트가 원하는 모델 지정 가능 (없으면 서버 기본값 사용)
- [x] **PDF 문제은행** — 업로드 시 SHA-256 해시 계산, PostgreSQL에 영구 저장, 동일 PDF 재업로드 시 LLM 호출 없이 즉시 반환
- [x] 3단계 비동기 콘텐츠 생성 파이프라인 (Notes → MCQ 배치 → Fill 배치)
- [x] **문제 레벨 1~5** — `difficulty: easy/medium/hard` 대신 정수 `level: 1~5` 체계. 레벨 정의: 1(기초암기)·2(개념이해)·3(시험최하)·4(표준시험)·5(고난도). 기존 저장 데이터 자동 변환 (easy→2, medium→3, hard→4)
- [x] **문제 유형 분류** — `question_type: concept | application` (개념문제 / 실습문제). MCQ·Fill 모두 적용
- [x] **생성 분포 상향** — L1:8% L2:12% L3:20% L4:35% L5:25% (기존 easy 30%/medium 50%/hard 20%에서 실전 시험 수준으로 상향)
- [x] 진행률 폴링 (`/status`, 0~100%)
- [x] Redis / 인메모리 세션 스토어 (2시간 TTL)
- [x] 응답 검증 (중복 제거, 환각 탐지, 필드 보정, level/question_type 범위 검증)
- [x] 서킷 브레이커 (5회 연속 실패 → 60초 차단)
- [x] 지수 백오프 재시도 (최대 2회)
- [x] Rate Limiting (IP당 분당 30회)
- [x] 구조화 JSON 로깅 + Prometheus 메트릭
- [x] Docker / Docker Compose (PostgreSQL 서비스 포함)
- [x] GitHub Actions CI/CD → AWS EC2 (ECR + SSM Parameter Store) 자동 배포 ([deploy/](deploy/) 폴더)
- [x] 단위 테스트 77개 (response_validator 26개 포함) + 통합 테스트 14개
- [x] **Supabase RS256 토큰 JWKS 검증** (2026-04-14, `c0d73ac`) — `app/core/auth.py`에서 토큰 헤더 `alg`를 먼저 읽어 비대칭(RS/ES/PS)이면 `iss` 기반 JWKS 엔드포인트에서 공개키를 받아 `kid` 매칭 후 검증, HS256이면 기존 `SUPABASE_JWT_SECRET` 경로 유지. JWKS 1시간 캐시 + `kid` 미스 시 1회 재조회로 키 로테이션 대응. JWKS fetch 실패는 503으로 반환
- [x] **세션 소유권 검증** (2026-04-14, `2dcb6ed`) — `/generate`·`/status`·`/result`·`/session` 전 엔드포인트에 `user_id` 기반 접근 제어
- [x] **입력 검증 강화** (2026-04-14) — `app/core/validators.py` 신규(UUID/API 키 형식), `app/models/schemas.py`에 Pydantic 필드 제약(길이·범위·패턴) 전면 적용
- [x] **보안 헤더 미들웨어** (2026-04-14) — `app/main.py`에 HSTS·X-Frame-Options·`X-Content-Type-Options: nosniff` 등 추가, CORS `allow_methods`/`allow_headers` 화이트리스트화
- [x] **에러 메시지 정보 노출 차단** (2026-04-14) — 에러 메시지에서 세션 ID 등 식별자 제거, 업로드 파일명 sanitize
- [x] **DB 자격증명·마이그레이션 정비** (2026-04-14) — `DATABASE_URL` 필수화, `docker-compose.yml`의 `POSTGRES_PASSWORD`를 `.env` 기반으로 전환, `user_store` SQL `::uuid` 캐스팅 및 과목 소유권 선검증, `migrations/001~003` 정비 및 `migrations/README.md` 추가
- [x] **세션 ID 단일화 & 상태 동기화** (2026-04-14, `d488f43`·`821ab87`) — 메모리 `session_store.session_id`와 Postgres `user_sessions.id`가 서로 달라 웹 대시보드가 영원히 pending으로 남던 문제 해결. `SessionCreate`에 optional `id` 필드, `user_store.upsert_session`으로 `ON CONFLICT (user_id, pdf_hash) DO UPDATE` 처리(재업로드 시 기존 row 재사용해 복습 일정·시도 내역 FK 보존), `/upload`가 upsert 반환 id를 메모리 레코드에도 동일하게 사용. `/generate` 완료·실패 시 `user_store.update_session_status`로 DB 행을 `ready`·`failed`로 동기화, 실패 경로 7곳을 `_fail()` 헬퍼로 통합
- [x] **`DELETE /user/sessions/{id}` 엔드포인트** (2026-04-14) — `user_store.delete_session`이 `user_sessions` 행 제거(복습 일정·시도 내역은 `ON DELETE CASCADE`로 함께 삭제), 메모리 `session_store`도 best-effort 정리. 웹 대시보드 휴지통 버튼에서 사용
- [x] **모바일 Supabase Auth 수용** (2026-04-15) — 백엔드 코드 변경 없음. 모바일이 `@supabase/supabase-js`로 발급받은 access token을 Bearer 헤더로 부착하면 기존 `core/auth.py`가 JWKS/HS256 분기 검증으로 그대로 수용. `/user/sync`는 로컬 SQLite → 클라우드 최초 업로드 경로로 재활용 (모바일 `migration.ts`에서 SecureStore `cloud_sync_completed_at` 플래그로 1회 실행)
- [x] **세션 영구 pending 버그 종합 수정** (2026-04-15) — 증상: 생성이 끝난 세션이 대시보드에서 계속 "생성 중"으로 남고 `/study/{id}` 재진입 불가. 원인: `_sync_user_session_status`가 예외·0 row matched를 조용히 삼켜 memory `complete` vs DB `pending` 불일치가 영구화. 수정: (1) sync를 bool 반환으로 바꾸고 실패 시 `logger.error` + memory store를 `failed`로 전이시키는 `_finalize_ready` 헬퍼 도입, (2) `upsert_session`의 `ON CONFLICT DO UPDATE` 절에 `status = CASE WHEN user_sessions.status IN ('ready','failed') THEN user_sessions.status ELSE EXCLUDED.status END` 로 재업로드 다운그레이드 차단, (3) `/result`에 memory miss 시 `user_sessions.pdf_hash → question_bank.content_json` fallback을 추가해 memory TTL·재시작과 무관하게 재진입 가능(두 DB 풀 분리 환경 대응), (4) `get_sessions` self-heal을 user_store 풀에서 pending pdf_hash 수집 → question_bank 풀에서 매치 조회 → user_store 풀에서 UPDATE 의 3단계 크로스 DB 흐름으로 재작성. `documents/problem/2026-04-15-session-stuck-pending.md`에 8개 가설 검증 기록

---

## 아키텍처

```
POST /upload   → pdfplumber 파싱 → 세션 스토어 (Redis / 인메모리)
POST /generate → plan 기반 분기 → 3단계 파이프라인 → 검증 → 세션 스토어
                  ├─ paid   → AnthropicClient (서킷 브레이커)
                  ├─ gpt    → OpenAIClient (서킷 브레이커, JSON mode)
                  └─ timely → TimelyClient (토큰 캐싱 + 서킷 브레이커)
GET  /status   → 생성 진행률 폴링 (0~100%)
GET  /result   → 완성된 StudyContent JSON 반환
DELETE /session → 세션 및 데이터 정리
GET  /health   → 헬스체크
GET  /metrics  → Prometheus 메트릭 (내부용)
```

### 디렉터리 구조

```
app/
├── core/
│   ├── config.py              # 환경변수 기반 설정 (pydantic-settings)
│   ├── exceptions.py          # 커스텀 예외 + 전역 에러 핸들러
│   └── logging_config.py      # 구조화 로깅 설정
├── models/
│   └── schemas.py             # Pydantic 데이터 모델
├── routers/
│   ├── upload.py              # POST /upload (SHA-256 해시 계산 포함)
│   └── generate.py            # POST /generate, GET /status, /result, DELETE /session
├── services/
│   ├── pdf_parser.py          # PDF 텍스트 추출 + 섹션 감지
│   ├── anthropic_client.py    # Claude API 래퍼 (재시도 + 서킷 브레이커)
│   ├── openai_client.py       # OpenAI GPT API 래퍼 (재시도 + 서킷 브레이커)
│   ├── timely_client.py       # TimelyGPT API 래퍼 (토큰 캐싱 + 재시도 + 서킷 브레이커)
│   ├── json_utils.py          # JSON 추출 + 부분 복구 공유 유틸리티
│   ├── prompt_builder.py      # 프롬프트 템플릿 생성
│   ├── response_validator.py  # LLM 출력 검증 + 정제
│   ├── session_store.py       # Redis/인메모리 세션 관리
│   └── question_bank.py       # PostgreSQL 문제은행 (asyncpg, pdf_hash 키)
└── main.py                    # FastAPI 앱 초기화
```

---

## 콘텐츠 생성 파이프라인

```
POST /generate 수신
  └─▶ 백그라운드 태스크 시작
        │
        ├─ [Stage 1] 학습 노트 생성 (progress 5% → 40%)
        │    ├─ paid:   AnthropicClient.generate_with_retry() (4096 tokens)
        │    ├─ gpt:    OpenAIClient.generate_with_retry() (4096 tokens)
        │    └─ timely: TimelyClient.generate_with_retry() (4096 tokens)
        │    └─▶ validate_notes() → StudyNotes 객체 + notes_dict(concept_id 맵)
        │
        ├─ [Stage 2] MCQ 배치 생성 (progress 40% → 80%)
        │    ├─ paid:   AnthropicClient.generate_with_retry() (8192 tokens, 1회)
        │    ├─ gpt:    OpenAIClient.generate_with_retry() (8192 tokens, 1회)
        │    └─ timely: TimelyClient.generate_with_retry() (8192 tokens, 1회)
        │    └─▶ validate_mcq() → 중복/환각 제거 후 MCQQuestion 목록
        │
        └─ [Stage 3] 빈칸 채우기 배치 생성 (progress 80% → 100%)
             ├─ paid:   AnthropicClient.generate_with_retry() (2048 tokens, 1회)
             ├─ gpt:    OpenAIClient.generate_with_retry() (2048 tokens, 1회)
             └─ timely: TimelyClient.generate_with_retry() (2048 tokens, 1회)
             └─▶ validate_fill() → FillQuestion 목록
             └─▶ StudyContent JSON → 세션 스토어 저장
             └─▶ question_bank.save_to_bank() — PostgreSQL 영구 저장
```

> **문제은행 캐시**: `/generate` 요청 시 `session.pdf_hash`로 `question_bank` 조회 → 히트 시 LLM 3단계 파이프라인 생략, 저장된 콘텐츠 즉시 반환. DB 연결 실패 시 기존 생성 플로우로 자동 폴백.

### 문제 수 자동 계산 (`calculate_question_counts`)

| 항목 | 공식 | 범위 |
|------|------|------|
| MCQ | `max(10, min(20, 섹션수 × 3))` | 10~20개 |
| 빈칸 | `max(6, min(15, 섹션수 × 2))` | 6~15개 |

---

## 데이터 모델 (`app/models/schemas.py`)

```
StudyContent
├── session_id: str
├── notes: StudyNotes
│   ├── key_concepts: List[KeyConcept]
│   │   └── id, term, definition, importance (high/medium/low)
│   ├── sections: List[StudySection]
│   │   └── title, summary, bullets[]
│   └── glossary: List[GlossaryEntry]
│       └── term, brief_def
├── mcq_questions: List[MCQQuestion]
│   └── id, question, options{A/B/C/D}, correct_answer, explanation, concept_id, difficulty
├── fill_questions: List[FillQuestion]
│   └── id, sentence_with_blank (___), answer, acceptable_variants[], hint, concept_id
└── metadata: ContentMetadata
    └── page_count, word_count, generated_at, model_used, section_count
```

---

## API Reference

### POST /upload

PDF 파일을 업로드합니다. 이후 `/generate` 호출에 사용할 `session_id`를 반환합니다.

**파라미터:**
- `file` Form 필드: 업로드할 PDF 파일
- `plan` Form 필드: `"paid"` / `"gpt"` / `"timely"` (기본값: `"paid"`)
- `X-API-Key` 헤더 또는 `api_key` Form 필드: 필수 (사용자가 직접 발급받은 키)

```bash
# 유료 플랜 (Anthropic Claude)
curl -X POST http://localhost:8000/upload \
  -H "X-API-Key: sk-ant-..." \
  -F "file=@document.pdf" \
  -F "plan=paid"
```

Response:
```json
{
  "session_id": "uuid",
  "pdf_name": "document.pdf",
  "page_count": 12,
  "word_count": 4200,
  "status": "uploaded"
}
```

### POST /generate

비동기 콘텐츠 생성을 시작합니다. 즉시 `status: "processing"` 반환 후 백그라운드 실행.

**Request Body:**
- `session_id` (필수): `/upload`에서 받은 세션 ID
- `plan` (선택): `"paid"` / `"gpt"` / `"timely"` (기본값: `"paid"`)

| plan | AI | API 키 |
|------|----|--------|
| `paid` | Anthropic Claude Sonnet | `sk-ant-...` |
| `gpt` | OpenAI GPT-4o-mini | `sk-...` |
| `timely` | TimelyGPT (50+ 모델) | timelygpt.co.kr 발급 키 |

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `session_id` | string | 필수 | 업로드 세션 ID |
| `plan` | string | `"paid"` | `paid` / `gpt` / `timely` |
| `lang` | string | `"ko"` | `ko` (한국어) / `en` (영어) — 생성 콘텐츠 언어 |

```bash
# Anthropic 유료 플랜
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk-ant-..." \
  -d '{"session_id": "uuid", "plan": "paid"}'

# OpenAI GPT 플랜
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk-..." \
  -d '{"session_id": "uuid", "plan": "gpt"}'

# TimelyGPT 플랜
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <timely-api-key>" \
  -d '{"session_id": "uuid", "plan": "timely"}'
```

### GET /status/{session_id}

생성 진행률을 폴링합니다 (3초 간격 권장).

```bash
curl http://localhost:8000/status/uuid
```

Response:
```json
{
  "session_id": "uuid",
  "status": "processing",
  "progress_pct": 45,
  "error_message": null
}
```

`status` 값: `uploaded` → `processing` → `done` / `failed`

### GET /result/{session_id}

완성된 StudyContent JSON을 반환합니다.

| 상태 | HTTP | 설명 |
|------|------|------|
| 완료 | 200 | StudyContent JSON |
| 처리 중 | 202 | 아직 생성 중 |
| 미시작 | 400 | generate 미호출 |
| 실패 | 500 | 생성 오류 |

### DELETE /session/{session_id}

세션과 연관 데이터를 삭제합니다.

### GET /health

```json
{ "status": "ok", "version": "1.0.0", "environment": "development" }
```

---

## Error Responses

```json
{ "error": "error_type", "message": "설명" }
```

| HTTP | error | 원인 |
|------|-------|------|
| 400 | `pdf_parse_error` | 비밀번호 보호, 스캔 PDF 등 |
| 400 | `validation_error` | 잘못된 요청 파라미터 |
| 401 | `generation_error` | 잘못된 API 키 (paid/gpt/timely 플랜) |
| 404 | `session_not_found` | 세션이 없거나 만료됨 |
| 413 | `pdf_parse_error` | 파일 크기 초과 (20MB) |
| 422 | `pdf_parse_error` | 텍스트 레이어 없는 스캔 PDF |
| 429 | `generation_error` | API 요청 한도 초과 (paid/gpt/timely 플랜) |
| 429 | *(slowapi)* | 서버 Rate Limit 초과 (IP당 30/분) |
| 500 | `internal_server_error` | 예상치 못한 서버 오류 |
| 502 | `generation_error` | AI API 오류 (paid/gpt/timely 플랜) |
| 503 | `generation_error` | 서킷 브레이커 작동 중 또는 API 쿼터 소진 |
| 504 | `generation_error` | AI API 타임아웃 |

---

## Prerequisites

- Python 3.11+
- Docker + Docker Compose
- (프로덕션) AWS 계정 (ECS, ECR, S3, ElastiCache)

## Quick Start — 로컬 개발

```bash
# 1. 환경 파일 복사
cp .env.example .env

# 2. Docker Compose로 실행 (Redis + PostgreSQL 포함)
docker-compose up

# 또는 직접 실행 (PostgreSQL이 별도로 실행 중이어야 함)
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**필수 환경변수 (.env)**
```
# 문제은행 DB (docker-compose 사용 시 자동 설정됨)
DATABASE_URL=postgresql://study@localhost:5432/studyhelper

# Redis (docker-compose 사용 시 자동 설정됨)
REDIS_URL=redis://localhost:6379

# Supabase (인증 + 사용자 데이터 — 필수)
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_JWT_SECRET=
SUPABASE_DB_URL=
```

LLM API 키 (`paid` / `gpt` / `timely`) 는 사용자가 요청 시점에 헤더로 직접 제공하므로 서버 환경변수에 둘 필요 없음.

- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`

---

## Configuration

### Anthropic (유료 플랜)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ENVIRONMENT` | `development` | `development` / `production` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | 사용할 Claude 모델 |
| `ANTHROPIC_TIMEOUT` | `30` | Anthropic API 타임아웃 (초) |
| `MAX_RETRIES` | `2` | Anthropic API 재시도 횟수 |

### 공통

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ALLOWED_ORIGINS` | `["http://localhost:3000","http://localhost:8081"]` | CORS 허용 출처 |
| `REDIS_URL` | `redis://localhost:6379` | Redis 연결 URL |
| `REDIS_TLS_ENABLED` | `false` | Redis TLS 활성화 |
| `DATABASE_URL` | `postgresql://study@localhost:5432/studyhelper` | PostgreSQL 연결 URL (문제은행) |
| `RATE_LIMIT_PER_MINUTE` | `30` | IP당 분당 최대 요청 수 |
| `MAX_PDF_PAGES` | `50` | 처리할 최대 PDF 페이지 수 |
| `MAX_PDF_SIZE_MB` | `20` | 최대 업로드 파일 크기 |

---

## QA — 테스트

### 테스트 구조 (총 64개)

```
tests/
├── unit/                                  # 50개
│   ├── test_pdf_parser.py        (7개)   PDF 파싱, 스캔 감지, 섹션 추출, 크기 검증
│   ├── test_response_validator.py (13개)  노트/MCQ/빈칸 검증, 중복 제거, 환각 탐지
│   ├── test_prompt_builder.py    (5개)   프롬프트 템플릿, 문제 수 자동 계산
│   ├── test_anthropic_client.py  (7개)   API 호출, 재시도 로직, 서킷 브레이커
│   ├── test_session_store.py     (9개)   Redis/인메모리 CRUD, TTL 동작
│   └── test_exceptions.py        (9개)   에러 핸들러, HTTP 상태 코드
└── integration/                           # 14개
    ├── test_api.py               (6개)   엔드포인트 기본 동작, 헬스체크
    └── test_generate_endpoint.py (8개)   /generate → /status → /result 파이프라인
```

### 테스트 실행

```bash
# 전체 테스트 + 커버리지
pytest tests/ -v --cov=app --cov-report=term-missing

# 단위 테스트만
pytest tests/unit/ -v

# 통합 테스트만
pytest tests/integration/ -v
```

---

## Security

- **CORS**: `ALLOWED_ORIGINS` 환경변수로 허용 출처 제한 (기본: localhost만)
- **Rate Limiting**: IP당 분당 30회 (slowapi)
- **API 키 (유료)**: 서버에 저장하지 않음 — `X-API-Key` 헤더로 전달 후 Anthropic에 포워딩
- **서킷 브레이커**: 5회 연속 실패 시 60초 Anthropic API 차단
- **비루트 컨테이너**: Dockerfile에서 `appuser`로 실행
- **Redis TLS**: `REDIS_TLS_ENABLED=true`로 활성화 (`rediss://` URL 사용)

---

## Observability

### 로깅

- **개발**: 사람이 읽기 쉬운 포맷 (`asctime level name message`)
- **프로덕션**: JSON 구조화 로깅 (CloudWatch / ELK 수집 가능)
- 모든 요청에 `X-Request-ID` 헤더 자동 부여

### 메트릭

`GET /metrics`에서 Prometheus 포맷 제공 (Swagger 비노출):
- 요청 수, 응답 시간, 상태 코드 분포
- Grafana 대시보드 또는 CloudWatch Container Insights 연동 가능

---

## Deployment (AWS)

### GitHub Secrets 설정

| Secret | 설명 |
|--------|------|
| `AWS_ACCESS_KEY_ID` | AWS IAM 키 |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM 시크릿 |

### CI/CD 파이프라인

`main` 브랜치에 push 시 자동 실행:

```
1. Test       → pytest + coverage 리포트 (codecov)
2. Security   → Trivy 취약점 스캔 + flake8 린팅
3. Build&Push → Docker 이미지 빌드 → ECR 푸시 (SHA 태그 + latest)
4. Deploy     → ECS 롤링 업데이트 (zero-downtime)
```

### ECS 구성

| 항목 | 값 |
|------|----|
| CPU | 512 units (스케일 시 2048) |
| Memory | 1024 MB |
| 최소 태스크 | 1 |
| 최대 태스크 | 10 |
| 스케일아웃 조건 | CPU > 70% (3분 지속) |
| 헬스체크 | `GET /health` → 200 |

---

## Data Privacy

- API 키 (Anthropic/OpenAI/TimelyGPT)는 요청별로 포워딩되며 서버에 저장되지 않음
- PDF는 S3에 임시 저장 후 자동 삭제 (라이프사이클 규칙 권장)
- 생성 결과는 Redis에 2시간 캐시 후 만료
- **문제은행**: 생성된 학습 콘텐츠(노트·MCQ·빈칸)는 PDF 해시(SHA-256) 키로 PostgreSQL에 영구 저장됨. 동일 PDF를 업로드한 다른 사용자에게도 같은 콘텐츠 제공 가능 — 업로드 전 사용자 동의 필요 (모바일 앱 처리)
- 사용자 식별 정보 미수집
