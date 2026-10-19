-- M2-B additive migration, applied after M1 by scripts/migrate_document_snapshots.py.
-- Transactional and repeatable. It changes no M1 statement: the M1 file and its
-- checksum stay as delivered. No content is migrated and no feature is enabled.

-- statement
-- A revision can now be "parsed": content stored, not indexed, never publishable.
ALTER TABLE document_revisions DROP CONSTRAINT IF EXISTS ck_revision_processing;
-- statement
ALTER TABLE document_revisions ADD CONSTRAINT ck_revision_processing
    CHECK (processing_state IN ('queued','processing','parsed','ready','partial','failed'));
-- statement
ALTER TABLE document_assets DROP CONSTRAINT IF EXISTS ck_asset_kind;
-- statement
ALTER TABLE document_assets ADD CONSTRAINT ck_asset_kind
    CHECK (kind IN ('embedded_image','figure_crop','page_preview','pdf_preview','formula_source','figure_preview'));
-- statement
ALTER TABLE document_blocks ADD COLUMN IF NOT EXISTS structure JSON;
-- statement
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS task_kind VARCHAR(32);
-- statement
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS source_version_id UUID;
-- statement
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS revision_id UUID;
-- statement
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS lock_token UUID;
-- statement
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS claimed_generation INTEGER;
-- statement
ALTER TABLE ingestion_jobs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMP WITH TIME ZONE;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_job_binding' AND conrelid='ingestion_jobs'::regclass) THEN ALTER TABLE ingestion_jobs ADD CONSTRAINT ck_job_binding CHECK ((task_kind IS NULL AND source_version_id IS NULL AND revision_id IS NULL) OR (task_kind = 'odp_parse' AND source_version_id IS NOT NULL AND revision_id IS NOT NULL)); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_job_source' AND conrelid='ingestion_jobs'::regclass) THEN ALTER TABLE ingestion_jobs ADD CONSTRAINT fk_job_source FOREIGN KEY(document_id, source_version_id) REFERENCES document_sources (document_id, id); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_job_revision' AND conrelid='ingestion_jobs'::regclass) THEN ALTER TABLE ingestion_jobs ADD CONSTRAINT fk_job_revision FOREIGN KEY(document_id, revision_id) REFERENCES document_revisions (document_id, id); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_job_revision' AND conrelid='ingestion_jobs'::regclass) THEN ALTER TABLE ingestion_jobs ADD CONSTRAINT uq_job_revision UNIQUE (revision_id); END IF; END $$;
-- statement
CREATE TABLE IF NOT EXISTS document_staged_objects (
	storage_key VARCHAR(1024) NOT NULL,
	document_id UUID NOT NULL,
	job_id UUID,
	purpose VARCHAR(32) NOT NULL,
	lease_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (storage_key),
	CONSTRAINT ck_staged_purpose CHECK (purpose IN ('upload_source','parse_asset'))
);
-- statement
CREATE INDEX IF NOT EXISTS ix_document_staged_objects_document_id ON document_staged_objects (document_id);
-- statement
-- A page whose preview mapping was not proven keeps no preview page number, so
-- no reader can quote a page that was never checked. Such a page cannot publish.
ALTER TABLE document_pages ALTER COLUMN preview_page_no DROP NOT NULL;
