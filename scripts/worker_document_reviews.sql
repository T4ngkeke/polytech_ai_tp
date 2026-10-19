-- Apply to edudb_worker_test first. No production migration has been executed.
CREATE TABLE IF NOT EXISTS document_reviews (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    file_sha256 VARCHAR(64) NOT NULL,
    source_key VARCHAR(64) NOT NULL,
    source_ref JSON NOT NULL,
    page_no INTEGER NOT NULL,
    bbox JSON NOT NULL,
    reason TEXT NOT NULL,
    candidate_content TEXT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    revision INTEGER NOT NULL DEFAULT 1,
    final_content TEXT,
    resolved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_document_review_source UNIQUE (document_id, source_key)
);
CREATE INDEX IF NOT EXISTS ix_document_reviews_document_id ON document_reviews(document_id);
