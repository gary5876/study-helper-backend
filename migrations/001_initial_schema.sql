-- 001: 초기 테이블 + RLS 정책
-- Supabase SQL Editor에서 실행 완료된 상태.

CREATE TABLE user_subjects (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    color      TEXT NOT NULL DEFAULT '#6c63ff',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, name)
);

CREATE TABLE user_sessions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    pdf_name      TEXT NOT NULL,
    pdf_hash      TEXT,
    subject_id    UUID REFERENCES user_subjects(id),
    page_count    INTEGER DEFAULT 0,
    word_count    INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'pending',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed TIMESTAMPTZ
);

CREATE TABLE user_review_schedule (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    session_id     UUID NOT NULL REFERENCES user_sessions(id) ON DELETE CASCADE,
    question_id    TEXT NOT NULL,
    question_type  TEXT NOT NULL,
    interval_days  INTEGER DEFAULT 1,
    next_review_at TIMESTAMPTZ NOT NULL,
    ease_factor    REAL DEFAULT 2.5,
    repetitions    INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'pending',
    UNIQUE(session_id, question_id)
);

CREATE TABLE user_attempts (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    session_id   UUID NOT NULL REFERENCES user_sessions(id) ON DELETE CASCADE,
    attempt_type TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    score_pct    REAL
);

ALTER TABLE user_subjects        ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_sessions        ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_review_schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_attempts        ENABLE ROW LEVEL SECURITY;

CREATE POLICY "본인 데이터만" ON user_subjects        FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "본인 데이터만" ON user_sessions        FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "본인 데이터만" ON user_review_schedule FOR ALL USING (auth.uid() = user_id);
CREATE POLICY "본인 데이터만" ON user_attempts        FOR ALL USING (auth.uid() = user_id);
