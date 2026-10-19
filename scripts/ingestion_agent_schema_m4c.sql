-- M4-C additive research persistence. Apply explicitly after M3-B.
-- No production feature is enabled by this migration alone.
-- statement
CREATE TABLE IF NOT EXISTS agent_runs (
 id UUID PRIMARY KEY,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
 client_request_id VARCHAR(128) NOT NULL,
 question TEXT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'running',
 snapshot_map JSON NOT NULL DEFAULT '{}',
 user_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
 assistant_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
 stop_reason VARCHAR(64),
 settlement_status VARCHAR(16) NOT NULL DEFAULT 'open',
 started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finished_at TIMESTAMPTZ,
 CONSTRAINT uq_agent_run_user_request UNIQUE(user_id,client_request_id),
 CONSTRAINT ck_agent_run_status CHECK (status IN ('running','cancelling','finished','failed','cancelled')),
 CONSTRAINT ck_agent_run_settlement CHECK (settlement_status IN ('open','settled'))
);
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_active_session ON agent_runs(session_id)
 WHERE status IN ('running','cancelling');
-- statement
CREATE TABLE IF NOT EXISTS message_citations (
 id UUID PRIMARY KEY,
 message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
 label VARCHAR(24) NOT NULL,
 document_id UUID NOT NULL,
 revision_id UUID NOT NULL,
 page_id UUID NOT NULL,
 pdf_page_no INTEGER NOT NULL,
 block_id UUID,
 range_start INTEGER,
 range_end INTEGER,
 asset_id UUID,
 origin VARCHAR(32) NOT NULL,
 availability VARCHAR(16) NOT NULL DEFAULT 'available',
 CONSTRAINT uq_message_citation_label UNIQUE(message_id,label),
 CONSTRAINT ck_message_citation_range CHECK
 ((range_start IS NULL AND range_end IS NULL) OR
  (range_start IS NOT NULL AND range_end IS NOT NULL AND range_start >= 0 AND range_end > range_start)),
 CONSTRAINT ck_message_citation_availability CHECK (availability IN ('available','unavailable')),
 CONSTRAINT ck_message_citation_page CHECK (pdf_page_no > 0)
);
-- statement
CREATE INDEX IF NOT EXISTS ix_message_citations_message_id ON message_citations(message_id);
-- statement
CREATE TABLE IF NOT EXISTS session_read_state (
 session_id UUID PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
 state_revision INTEGER NOT NULL DEFAULT 0,
 focused_sources JSON NOT NULL DEFAULT '[]',
 last_used_citation_ids JSON NOT NULL DEFAULT '[]',
 open_question JSON NOT NULL DEFAULT '{}',
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- statement
CREATE TABLE IF NOT EXISTS model_call_usage (
 call_id UUID PRIMARY KEY,
 run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
 purpose VARCHAR(32) NOT NULL,
 model VARCHAR(255) NOT NULL,
 input_tokens INTEGER,
 output_tokens INTEGER,
 usage_source VARCHAR(16) NOT NULL,
 token_alpha DOUBLE PRECISION NOT NULL,
 token_beta DOUBLE PRECISION NOT NULL,
 billed_tokens INTEGER NOT NULL,
 elapsed_seconds DOUBLE PRECISION NOT NULL,
 status VARCHAR(24) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_model_call_usage_source CHECK (usage_source IN ('actual','estimated','none')),
 CONSTRAINT ck_model_call_cost CHECK (billed_tokens >= 0 AND elapsed_seconds >= 0)
);
-- statement
CREATE INDEX IF NOT EXISTS ix_model_call_usage_run_id ON model_call_usage(run_id);
-- statement
CREATE TABLE IF NOT EXISTS usage_reservations (
 call_id UUID PRIMARY KEY,
 run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
 user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 date DATE NOT NULL,
 reserved_tokens INTEGER NOT NULL,
 estimated_input_tokens INTEGER NOT NULL,
 max_output_tokens INTEGER NOT NULL,
 token_alpha DOUBLE PRECISION NOT NULL,
 token_beta DOUBLE PRECISION NOT NULL,
 purpose VARCHAR(32) NOT NULL,
 model VARCHAR(255) NOT NULL,
 settled_tokens INTEGER,
 state VARCHAR(16) NOT NULL DEFAULT 'active',
 expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_usage_reservation_state CHECK (state IN ('active','unknown','settled')),
 CONSTRAINT ck_usage_reservation_amount CHECK (reserved_tokens >= 0)
);
-- statement
CREATE INDEX IF NOT EXISTS ix_usage_reservations_run_id ON usage_reservations(run_id);
-- statement
CREATE INDEX IF NOT EXISTS ix_usage_reservations_user_id ON usage_reservations(user_id);
