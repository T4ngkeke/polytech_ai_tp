-- M4-Z additive: suggested next questions under an answer (follow-up
-- generation, ported from Open WebUI). Apply after M4-Y.
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS follow_ups JSON;
