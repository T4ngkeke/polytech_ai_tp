-- M4-L additive: the guided tour starts by itself until the user has seen it.
-- Apply after M4-K.
-- statement
ALTER TABLE users ADD COLUMN IF NOT EXISTS tour_seen_at TIMESTAMPTZ;
