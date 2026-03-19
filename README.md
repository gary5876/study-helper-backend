# Fundamentals — Backend API

FastAPI 백엔드. PDF를 받아 Anthropic Claude로 학습 콘텐츠를 생성합니다.

## Architecture

```
POST /upload   → pdfplumber 파싱 → 세션 스토어 (Redis / 인메모리)
POST /generate → Anthropic API (3단계 프롬프트) → 검증 → 세션 스토어
GET  /status   → 생성 진행률 폴링 (0~100%)
GET  /result   → 완성된 StudyContent JSON 반환
DELETE /session → 세션 및 데이터 정리
GET  /health   → 헬스체크
GET  /metrics  → Prometheus 메트릭 (내부용)
```

## Prerequisites

- Python 3.11+
- Docker + Docker Compose
- (프로덕션) AWS 계정 (ECS, ECR, S3, ElastiCache)

## Quick Start — Local Development

```bash
# 1. 환경 파일 복사
cp .env.example .env
# .env에서 필요한 값 설정 (ALLOWED_ORIGINS 등)

# 2. Docker Compose로 실행 (Redis 포함)
docker-compose up

# 또는 직접 실행:
pip install -r requirements.txt
uvicorn app.main:app --reload
```

API: `http://localhost:8000`
Swagger UI: `http://localhost:8000/docs`

## Configuration

`.env.example`을 복사해 `.env`로 사용합니다. 주요 환경변수:

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `ENVIRONMENT` | `development` | `development` / `production` |
| `ALLOWED_ORIGINS` | `["http://localhost:3000","http://localhost:8081"]` | CORS 허용 출처 (JSON 배열) |
| `REDIS_URL` | `redis://localhost:6379` | Redis 연결 URL |
| `REDIS_TLS_ENABLED` | `false` | Redis TLS 활성화 (`rediss://` URL과 함께 사용) |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | 사용할 Claude 모델 |
| `ANTHROPIC_TIMEOUT` | `30` | Anthropic API 호출 타임아웃 (초) |
| `RATE_LIMIT_PER_MINUTE` | `30` | IP당 분당 최대 요청 수 |
| `MAX_PDF_PAGES` | `50` | 처리할 최대 PDF 페이지 수 |
| `MAX_PDF_SIZE_MB` | `20` | 최대 업로드 파일 크기 |

## API Reference

### POST /upload

PDF 파일을 업로드합니다. 이후 `/generate` 호출에 사용할 `session_id`를 반환합니다.

**API 키 전달 방법 (둘 중 하나):**
- `X-API-Key` 헤더 (권장)
- `api_key` Form 필드 (하위 호환)

```bash
# 헤더 방식 (권장)
curl -X POST http://localhost:8000/upload \
  -H "X-API-Key: sk-ant-..." \
  -F "file=@document.pdf"

# Form 방식 (하위 호환)
curl -X POST http://localhost:8000/upload \
  -F "file=@document.pdf" \
  -F "api_key=sk-ant-..."
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

비동기 콘텐츠 생성을 시작합니다.

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: sk-ant-..." \
  -d '{"session_id": "uuid"}'
```

### GET /status/{session_id}

생성 진행률을 폴링합니다 (0~100%).

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

### GET /result/{session_id}

완성된 StudyContent (노트 + MCQ + 빈칸 채우기)를 반환합니다.
아직 처리 중이면 `202`, 실패 시 `500`을 반환합니다.

### DELETE /session/{session_id}

세션과 연관 데이터를 삭제합니다.

## Error Responses

모든 에러는 아래 형식으로 반환됩니다:

```json
{
  "error": "error_type",
  "message": "Human-readable description"
}
```

| HTTP | error | 원인 |
|------|-------|------|
| 400 | `pdf_parse_error` | 비밀번호 보호, 스캔 PDF 등 |
| 400 | `validation_error` | 잘못된 요청 파라미터 |
| 401 | `generation_error` | 잘못된 Anthropic API 키 |
| 404 | `session_not_found` | 세션이 없거나 만료됨 |
| 413 | `pdf_parse_error` | 파일 크기 초과 |
| 422 | `pdf_parse_error` | 텍스트 레이어 없는 스캔 PDF |
| 429 | `generation_error` | Anthropic API 요청 한도 초과 |
| 429 | *(slowapi)* | 서버 Rate Limit 초과 (IP당 30/분) |
| 500 | `internal_server_error` | 예상치 못한 서버 오류 |
| 502 | `generation_error` | Anthropic API 오류 |
| 503 | `generation_error` | 서킷 브레이커 작동 중 |
| 504 | `generation_error` | Anthropic API 타임아웃 |

## Running Tests

```bash
# 전체 테스트 + 커버리지
pytest tests/ -v --cov=app --cov-report=term-missing

# 단위 테스트만
pytest tests/unit/ -v

# 통합 테스트만
pytest tests/integration/ -v
```

### 테스트 구조

```
tests/
├── unit/
│   ├── test_pdf_parser.py        # PDF 파싱 (7개)
│   ├── test_response_validator.py # 응답 검증 (13개)
│   ├── test_prompt_builder.py     # 프롬프트 생성 (5개)
│   ├── test_anthropic_client.py   # API 클라이언트 + 재시도 (7개)
│   ├── test_session_store.py      # 세션 스토어 CRUD (9개)
│   └── test_exceptions.py         # 예외 핸들러 (9개)
└── integration/
    ├── test_api.py                # 엔드포인트 기본 (6개)
    └── test_generate_endpoint.py  # /generate /status /result (8개)
```

## Security

- **CORS**: `ALLOWED_ORIGINS` 환경변수로 허용 출처 제한 (기본: localhost만)
- **Rate Limiting**: IP당 분당 30회 (slowapi)
- **API 키**: 서버에 저장하지 않음. `X-API-Key` 헤더로 전달 후 Anthropic에 포워딩
- **서킷 브레이커**: 5회 연속 실패 시 60초 Anthropic API 차단
- **비루트 컨테이너**: Dockerfile에서 `appuser`로 실행
- **Redis TLS**: `REDIS_TLS_ENABLED=true` 로 활성화 (`rediss://` URL 사용)

## Observability

### 로깅
- **개발**: 사람이 읽기 쉬운 포맷 (`asctime level name message`)
- **프로덕션**: JSON 구조화 로깅 (CloudWatch / ELK 수집 가능)
- 모든 요청에 `X-Request-ID` 헤더 자동 부여

### 메트릭
`GET /metrics`에서 Prometheus 포맷으로 제공 (swagger 노출 안 됨):
- 요청 수, 응답 시간, 상태 코드 분포
- Grafana 대시보드 또는 CloudWatch Container Insights와 연동 가능

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

수동 롤백: GitHub Actions `workflow_dispatch`로 트리거

### ECS 구성

| 항목 | 값 |
|------|----|
| CPU | 512 units (스케일 시 2048) |
| Memory | 1024 MB |
| 최소 태스크 | 1 |
| 최대 태스크 | 10 |
| 스케일아웃 조건 | CPU > 70% (3분 지속) |
| 헬스체크 | `GET /health` → 200 |

## Data Privacy

- Anthropic API 키는 요청별로 포워딩되며 서버에 저장되지 않음
- PDF는 S3에 임시 저장 후 자동 삭제 (라이프사이클 규칙 권장)
- 생성 결과는 Redis에 2시간 캐시 후 만료
- 사용자 식별 정보 미수집
