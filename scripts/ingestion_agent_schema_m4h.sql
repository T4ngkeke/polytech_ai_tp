-- M4-H additive: a queued job may wait until a given time. A teacher's block
-- edits are indexed once the teacher has stopped editing for a short while, so
-- a series of edits makes one new version, not one per edit. Apply after M4-G.
-- statement
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS not_before TIMESTAMPTZ;
