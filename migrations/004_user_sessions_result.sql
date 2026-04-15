-- 004: user_sessions를 결과 영속화의 primary 저장소로 승격.
--
-- 배경:
--   기존에는 생성된 StudyContent(notes+mcq+fill JSON)가 오직 question_bank 테이블
--   (DATABASE_URL 풀, user 데이터와 별개 DB)에만 저장되었음. Redis 메모리 store TTL
--   (2h) 만료 후에는 question_bank를 거치는 fallback에 전적으로 의존했는데,
--   save_to_bank 실패 시 조용히 warning으로 삼켜 "user_sessions.status='ready'지만
--   어디에도 결과가 없는" 고아 상태가 발생했다.
--
-- 이번 변경으로 user_sessions가 본인의 결과를 직접 들고, question_bank는 "같은 PDF를
-- 여러 사용자가 업로드했을 때 LLM 재호출을 건너뛰는 공유 캐시"로만 의미를 축소한다.

ALTER TABLE user_sessions
    ADD COLUMN IF NOT EXISTS result_json   TEXT,
    ADD COLUMN IF NOT EXISTS error_message TEXT,
    ADD COLUMN IF NOT EXISTS completed_at  TIMESTAMPTZ;

-- 대시보드 목록 조회와 상태 집계를 가속하기 위한 인덱스.
-- user_id로 먼저 좁힌 후 status를 보는 것이 압도적으로 흔한 패턴.
CREATE INDEX IF NOT EXISTS idx_user_sessions_user_status
    ON user_sessions(user_id, status);
