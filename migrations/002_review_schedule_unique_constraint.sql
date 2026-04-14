-- 002: review_schedule UNIQUE 제약 조건에 user_id 추가
-- 이유: 기존 (session_id, question_id)만으로는 다른 사용자의 복습 스케줄을 덮어쓸 수 있음
-- 적용 대상: user_review_schedule 테이블

-- 1) 기존 UNIQUE 제약 조건 삭제
ALTER TABLE user_review_schedule
    DROP CONSTRAINT IF EXISTS user_review_schedule_session_id_question_id_key;

-- 2) user_id를 포함한 새 UNIQUE 제약 조건 추가
ALTER TABLE user_review_schedule
    ADD CONSTRAINT user_review_schedule_user_session_question_key
    UNIQUE (user_id, session_id, question_id);
