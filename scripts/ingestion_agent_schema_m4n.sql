-- M4-N additive: a teacher can ignore a reset request instead of resetting
-- the limits; the request keeps how it was answered. Older answered requests
-- were all resets. Apply after M4-M.
-- statement
ALTER TABLE quota_reset_requests ADD COLUMN IF NOT EXISTS outcome VARCHAR(8);
-- statement
UPDATE quota_reset_requests SET outcome = 'reset' WHERE resolved_at IS NOT NULL AND outcome IS NULL;
