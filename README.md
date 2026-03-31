# Study Helper — Backend API

FastAPI 백엔드. PDF를 받아 AI(Anthropic Claude / OpenAI GPT / TimelyGPT / Google Gemini)로 학습 콘텐츠(노트 · MCQ · 빈칸 채우기)를 생성합니다.

---

## 현재 상태 (2026-03-31)

### 완성된 기능

- [x] PDF 업로드 + pdfplumber 파싱 (최대 50페이지 / 20MB)
- [x] **무료 플랜** — Google Gemini 2.0 Flash (키 풀 라운드로빈 + Retry-After 우선 읽기 + 키별 지수 백오프 + 전체 쿨다운 시 대기 후 재시도)
- [x] **유료 플랜 (paid)** — Anthropic Claude Sonnet (서킷 브레이커 + 지수 백오프 재시도)
- [x] **GPT 플랜 (gpt)** — OpenAI GPT-4o-mini / GPT-4o 등 (서킷 브레이커 + 재시도, JSON mode)
- [x] **TimelyGPT 플랜 (timely)** — TimelyGPT (timelygpt.co.kr) API 키로 50+ 모델 선택 (토큰 캐싱 55분 + 서킷 브레이커 + 재시도)
- [x] 3단계 비동기 콘텐츠 생성 파이프라인 (Notes → MCQ 배치 → Fill 배치)
- [x] Gemini MCQ/Fill 배치 분할 생성 (MCQ 5개/배치, Fill 8개/배치 — 토큰 한도 대응)
- [x] 진행률 폴링 (`/status`, 0~100%)
- [x] Redis / 인메모리 세션 스토어 (2시간 TTL)
- [x] 응답 검증 (중복 제거, 환각 탐지, 필드 보정)
- [x] 서킷 브레이커 (5회 연속 실패 → 60초 차단)
- [x] 지수 백오프 재시도 (최대 2회)
- [x] Rate Limiting (IP당 분당 30회)
- [x] 구조화 JSON 로깅 + Prometheus 메트릭
- [x] Docker / Docker Compose
- [x] GitHub Actions CI/CD → AWS ECS 자동 배포
- [x] 단위 테스트 50개 + 통합 테스트 14개

---

## 아키텍처

```
POST /upload   → pdfplumber 파싱 → 세션 스토어 (Redis / 인메모리)
POST /generate → plan 기반 분기 → 3단계 파이프라인 → 검증 → 세션 스토어
                  ├─ free   → GeminiClient (키 풀, 배치 분할)
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
│   ├── upload.py              # POST /upload
│   └── generate.py            # POST /generate, GET /status, /result, DELETE /session
├── services/
│   ├── pdf_parser.py          # PDF 텍스트 추출 + 섹션 감지
│   ├── anthropic_client.py    # Claude API 래퍼 (재시도 + 서킷 브레이커)
│   ├── openai_client.py       # OpenAI GPT API 래퍼 (재시도 + 서킷 브레이커)
│   ├── timely_client.py       # TimelyGPT API 래퍼 (토큰 캐싱 + 재시도 + 서킷 브레이커)
│   ├── llm_provider.py        # Gemini 키 풀 + 배치 생성 클라이언트
│   ├── json_utils.py          # JSON 추출 + 부분 복구 공유 유틸리티
│   ├── prompt_builder.py      # 프롬프트 템플릿 생성
│   ├── response_validator.py  # LLM 출력 검증 + 정제
│   └── session_store.py       # Redis/인메모리 세션 관리
└── main.py                    # FastAPI 앱 초기화
```

---

## 콘텐츠 생성 파이프라인

```
POST /generate 수신
  └─▶ 백그라운드 태스크 시작
        │
        ├─ [Stage 1] 학습 노트 생성 (progress 5% → 40%)
        │    ├─ free:   GeminiClient.generate_notes() (4096 tokens)
        │    ├─ paid:   AnthropicClient.generate_with_retry() (4096 tokens)
        │    ├─ gpt:    OpenAIClient.generate_with_retry() (4096 tokens)
        │    └─ timely: TimelyClient.generate_with_retry() (4096 tokens)
        │    └─▶ validate_notes() → StudyNotes 객체 + notes_dict(concept_id 맵)
        │
        ├─ [Stage 2] MCQ 배치 생성 (progress 40% → 80%)
        │    ├─ free:   GeminiClient.generate_mcq_batched() (5개/배치, 최대 4회 순차 호출)
        │    ├─ paid:   AnthropicClient.generate_with_retry() (8192 tokens, 1회)
        │    ├─ gpt:    OpenAIClient.generate_with_retry() (8192 tokens, 1회)
        │    └─ timely: TimelyClient.generate_with_retry() (8192 tokens, 1회)
        │    └─▶ validate_mcq() → 중복/환각 제거 후 MCQQuestion 목록
        │
        └─ [Stage 3] 빈칸 채우기 배치 생성 (progress 80% → 100%)
             ├─ free:   GeminiClient.generate_fill_batched() (8개/배치, 최대 2회 순차 호출)
             ├─ paid:   AnthropicClient.generate_with_retry() (2048 tokens, 1회)
             ├─ gpt:    OpenAIClient.generate_with_retry() (2048 tokens, 1회)
             └─ timely: TimelyClient.generate_with_retry() (2048 tokens, 1회)
             └─▶ validate_fill() → FillQuestion 목록
             └─▶ StudyContent JSON → 세션 스토어 저장
```

> **Gemini 배치 중복 방지**: 각 배치 프롬프트에 이전 배치의 question/answer 텍스트를 주입해 중복을 1차 방지하고, validate_mcq의 Jaccard 70% 유사도 필터로 2차 제거합니다.

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
- `plan` Form 필드: `"free"` / `"paid"` / `"gpt"` / `"timely"` (기본값: `"paid"`)
- `X-API-Key` 헤더 또는 `api_key` Form 필드: 유료 플랜만 필요

```bash
# 유료 플랜
curl -X POST http://localhost:8000/upload \
  -H "X-API-Key: sk-ant-..." \
  -F "file=@document.pdf" \
  -F "plan=paid"

# 무료 플랜 (API 키 불필요)
curl -X POST http://localhost:8000/upload \
  -F "file=@document.pdf" \
  -F "plan=free"
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
- `plan` (선택): `"free"` / `"paid"` / `"gpt"` / `"timely"` (기본값: `"paid"`)

| plan | AI | API 키 |
|------|----|--------|
| `free` | Google Gemini 2.0 Flash | 불필요 |
| `paid` | Anthropic Claude Sonnet | `sk-ant-...` |
| `gpt` | OpenAI GPT-4o-mini | `sk-...` |
| `timely` | TimelyGPT (50+ 모델) | timelygpt.co.kr 발급 키 |

```bash
# 무료 플랜 (API 키 불필요)
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"session_id": "uuid", "plan": "free"}'

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

# 2. Docker Compose로 실행 (Redis 포함)
docker-compose up

# 또는 직접 실행
pip install -r requirements.txt
uvicorn app.main:app --reload
```

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

### Google Gemini (무료 플랜)

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `GEMINI_API_KEYS` | *(필수)* | 쉼표 구분 Gemini API 키 목록 (`키1,키2,키3`) |
| `GEMINI_MODEL` | `gemini-2.0-flash` | 사용할 Gemini 모델 |
| `GEMINI_TIMEOUT` | `60` | Gemini API 타임아웃 (초) |

#### Gemini Rate Limit 쿨다운 전략

| 상수 | 값 | 설명 |
|------|----|------|
| `_COOLDOWN_BASE_SECONDS` | `15` | 첫 번째 rate-limit 쿨다운 |
| `_COOLDOWN_MAX_SECONDS` | `60` | 키당 최대 쿨다운 (cap) |
| `_COOLDOWN_MULTIPLIER` | `2` | 연속 hit마다 2배 (15 → 30 → 60) |
| `_ALL_KEYS_MAX_WAIT` | `30` | 모든 키 쿨다운 시 최대 대기 시간 |

- **Retry-After 우선**: 429 응답에 `retryDelay` 값이 있으면 그 값을 쿨다운에 사용
- **키별 지수 백오프**: Retry-After 없으면 연속 hit 횟수에 따라 15s → 30s → 60s, 성공 시 리셋
- **즉시 로테이션**: 한 키가 막히는 즉시 다음 키로 전환 (대기 없음)
- **마지막 수단 대기**: 모든 키 쿨다운 시 가장 빨리 풀리는 키까지 대기 후 1회 재시도 (30초 초과 시 즉시 에러)

### 공통

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ALLOWED_ORIGINS` | `["http://localhost:3000","http://localhost:8081"]` | CORS 허용 출처 |
| `REDIS_URL` | `redis://localhost:6379` | Redis 연결 URL |
| `REDIS_TLS_ENABLED` | `false` | Redis TLS 활성화 |
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
- **Gemini 키 (무료)**: 서버 환경변수로만 관리, 클라이언트에 노출되지 않음
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

- Anthropic API 키는 요청별로 포워딩되며 서버에 저장되지 않음
- PDF는 S3에 임시 저장 후 자동 삭제 (라이프사이클 규칙 권장)
- 생성 결과는 Redis에 2시간 캐시 후 만료
- 사용자 식별 정보 미수집
