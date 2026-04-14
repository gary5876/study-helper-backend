-- 003: user_sessions에 (user_id, pdf_hash) UNIQUE 제약 조건 추가
-- 이유: sync_sessions의 ON CONFLICT가 동작하려면 필요.
--       같은 사용자가 같은 PDF를 중복 업로드하는 것도 방지.

ALTER TABLE user_sessions
    ADD CONSTRAINT user_sessions_user_id_pdf_hash_key
    UNIQUE (user_id, pdf_hash);
