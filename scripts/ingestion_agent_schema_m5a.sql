-- M5-A additive: the rolling summary of a long conversation, stored on the
-- first question still sent (context compaction, ported from Open WebUI).
-- Apply after M4-Z.
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS context_summary TEXT;
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS context_summary_snapshot JSON;
