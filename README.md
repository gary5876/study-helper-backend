# Study Helper — Backend API

FastAPI 백엔드. PDF를 받아 Anthropic Claude로 학습 콘텐츠(노트 · MCQ · 빈칸 채우기)를 생성합니다.

---

## 현재 상태 (2026-03-22)

### 완성된 기능

- [x] PDF 업로드 + pdfplumber 파싱 (최대 50페이지 / 20MB)
- [x] 3단계 비동기 콘텐츠 생성 (학습 노트 → MCQ → 빈칸 채우기)
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
POST /generate → 3단계 Anthropic 파이프라인 → 검증 → 세션 스토어
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
        ├─ [1단계] 학습 노트 생성 (progress 5% → 40%)
        │    └─▶ build_notes_prompt() → Claude API (4096 tokens max)
        │    └─▶ validate_notes() → StudyNotes 객체
        │
        ├─ [2단계] MCQ 생성 (progress 40% → 80%)
        │    └─▶ build_mcq_prompt(notes) → Claude API (8192 tokens max)
        │    └─▶ validate_mcq() → 중복/환각 제거 후 MCQQuestion 목록
        │
        └─ [3단계] 빈칸 채우기 생성 (progress 80% → 100%)
             └─▶ build_fill_prompt(notes) → Claude API (2048 tokens max)
             └─▶ validate_fill() → FillQuestion 목록
             └─▶ StudyContent JSON → 세션 스토어 저장
```

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

**API 키 전달 방법:**
- `X-API-Key` 헤더 (권장)
- `api_key` Form 필드 (하위 호환)

```bash
curl -X POST http://localhost:8000/upload \
  -H "X-API-Key: sk-ant-..." \
  -F "file=@document.pdf"
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

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk-ant-..." \
  -d '{"session_id": "uuid"}'
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
| 401 | `generation_error` | 잘못된 Anthropic API 키 |
| 404 | `session_not_found` | 세션이 없거나 만료됨 |
| 413 | `pdf_parse_error` | 파일 크기 초과 (20MB) |
| 422 | `pdf_parse_error` | 텍스트 레이어 없는 스캔 PDF |
| 429 | `generation_error` | Anthropic API 요청 한도 초과 |
| 429 | *(slowapi)* | 서버 Rate Limit 초과 (IP당 30/분) |
| 500 | `internal_server_error` | 예상치 못한 서버 오류 |
| 502 | `generation_error` | Anthropic API 오류 |
| 503 | `generation_error` | 서킷 브레이커 작동 중 |
| 504 | `generation_error` | Anthropic API 타임아웃 |

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

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ENVIRONMENT` | `development` | `development` / `production` |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | 사용할 Claude 모델 |
| `ANTHROPIC_TIMEOUT` | `30` | Anthropic API 타임아웃 (초) |
| `ALLOWED_ORIGINS` | `["http://localhost:3000","http://localhost:8081"]` | CORS 허용 출처 |
| `REDIS_URL` | `redis://localhost:6379` | Redis 연결 URL |
| `REDIS_TLS_ENABLED` | `false` | Redis TLS 활성화 |
| `RATE_LIMIT_PER_MINUTE` | `30` | IP당 분당 최대 요청 수 |
| `MAX_PDF_PAGES` | `50` | 처리할 최대 PDF 페이지 수 |
| `MAX_PDF_SIZE_MB` | `20` | 최대 업로드 파일 크기 |
| `MAX_RETRIES` | `2` | Anthropic API 재시도 횟수 |

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
- **API 키**: 서버에 저장하지 않음 — `X-API-Key` 헤더로 전달 후 Anthropic에 포워딩
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
