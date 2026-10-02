-- M2-C additive migration: structured review binding. Applied after M1 and M2-B
-- by scripts/migrate_document_snapshots.py. Transactional and repeatable.
-- Legacy PDF reviews keep their exact matching and uniqueness.

-- statement
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS source_version_id UUID;
-- statement
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS revision_id UUID;
-- statement
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS block_id UUID;
-- statement
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS kind VARCHAR(32);
-- statement
-- One review per source key: per document for legacy rows, per revision for
-- structured rows (a new revision may review the same block again).
ALTER TABLE document_reviews DROP CONSTRAINT IF EXISTS uq_document_review_source;
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_legacy_source ON document_reviews (document_id, source_key) WHERE revision_id IS NULL;
-- statement
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_revision_source ON document_reviews (revision_id, source_key) WHERE revision_id IS NOT NULL;
-- statement
CREATE INDEX IF NOT EXISTS ix_document_reviews_revision_id ON document_reviews (revision_id);
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_review_binding' AND conrelid='document_reviews'::regclass) THEN ALTER TABLE document_reviews ADD CONSTRAINT ck_review_binding CHECK ((revision_id IS NULL AND source_version_id IS NULL AND block_id IS NULL AND kind IS NULL) OR (revision_id IS NOT NULL AND source_version_id IS NOT NULL AND kind IN ('teacher_edit_unmatched','vision_description'))); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_review_revision' AND conrelid='document_reviews'::regclass) THEN ALTER TABLE document_reviews ADD CONSTRAINT fk_review_revision FOREIGN KEY(document_id, revision_id) REFERENCES document_revisions (document_id, id); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_review_source' AND conrelid='document_reviews'::regclass) THEN ALTER TABLE document_reviews ADD CONSTRAINT fk_review_source FOREIGN KEY(document_id, source_version_id) REFERENCES document_sources (document_id, id); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_review_block' AND conrelid='document_reviews'::regclass) THEN ALTER TABLE document_reviews ADD CONSTRAINT fk_review_block FOREIGN KEY(revision_id, block_id) REFERENCES document_blocks (revision_id, block_id); END IF; END $$;
