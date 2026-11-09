-- M4-I additive: quotas per day and per week, reset requests, refused
-- questions, the answer mode of a run and the time a PDF took to read.
-- Apply after M4-H. Quotas that still have the old default (50 000 a day)
-- get the new ones; a quota an admin changed is kept.
-- statement
ALTER TABLE users ADD COLUMN IF NOT EXISTS weekly_token_quota INTEGER NOT NULL DEFAULT 25000000;
-- statement
UPDATE users SET daily_token_quota = 5000000 WHERE daily_token_quota = 50000;
-- statement
ALTER TABLE quota_grants ADD COLUMN IF NOT EXISTS kind VARCHAR(8) NOT NULL DEFAULT 'day';
-- statement
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_quota_grant_kind') THEN
    ALTER TABLE quota_grants ADD CONSTRAINT ck_quota_grant_kind CHECK (kind IN ('day','week'));
  END IF;
END $$;
-- statement
CREATE TABLE IF NOT EXISTS quota_reset_requests (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date DATE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  resolved_at TIMESTAMPTZ,
  resolved_by UUID REFERENCES users(id) ON DELETE SET NULL
);
-- statement
CREATE INDEX IF NOT EXISTS ix_quota_reset_requests_user_date ON quota_reset_requests (user_id, date);
-- statement
CREATE TABLE IF NOT EXISTS chat_refusals (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  lab_id UUID REFERENCES labs(id) ON DELETE SET NULL,
  reason VARCHAR(32) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
-- statement
CREATE INDEX IF NOT EXISTS ix_chat_refusals_created_at ON chat_refusals (created_at);
-- statement
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS mode VARCHAR(16);
-- statement
ALTER TABLE document_revisions ADD COLUMN IF NOT EXISTS parse_started_at TIMESTAMPTZ;
-- statement
ALTER TABLE document_revisions ADD COLUMN IF NOT EXISTS parse_finished_at TIMESTAMPTZ;
