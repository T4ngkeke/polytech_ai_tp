-- M1 additive migration. Apply with scripts/migrate_document_snapshots.py.
-- Transactional, repeatable; never migrates legacy content or enables features.

-- statement
ALTER TABLE documents ADD COLUMN IF NOT EXISTS lifecycle VARCHAR(16) DEFAULT 'active' NOT NULL;
-- statement
ALTER TABLE documents ADD COLUMN IF NOT EXISTS generation INTEGER DEFAULT '0' NOT NULL;
-- statement
ALTER TABLE documents ADD COLUMN IF NOT EXISTS current_revision_id UUID;
-- statement
ALTER TABLE documents ADD COLUMN IF NOT EXISTS structured BOOLEAN DEFAULT 'false' NOT NULL;
-- statement
ALTER TABLE documents ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP WITH TIME ZONE;
-- statement
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS revision_id UUID;
-- statement
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS block_id UUID;
-- statement
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS part_index INTEGER;
-- statement
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS part_count INTEGER;
-- statement
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS content_start INTEGER;
-- statement
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS content_end INTEGER;
-- statement
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS token_estimate INTEGER;
-- statement
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS chunker_version VARCHAR(64);
-- statement
CREATE TABLE IF NOT EXISTS document_sources (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	format VARCHAR(16) NOT NULL,
	original_filename VARCHAR(512) NOT NULL,
	storage_key VARCHAR(1024) NOT NULL,
	replaces_source_id UUID,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_source_document UNIQUE (document_id, id),
	CONSTRAINT uq_source_hash UNIQUE (document_id, sha256),
	CONSTRAINT fk_source_replaces FOREIGN KEY(document_id, replaces_source_id) REFERENCES document_sources (document_id, id),
	FOREIGN KEY(document_id) REFERENCES documents (id),
	UNIQUE (storage_key),
	FOREIGN KEY(created_by) REFERENCES users (id)
);
-- statement
CREATE INDEX IF NOT EXISTS ix_document_sources_document_id ON document_sources (document_id);
-- statement
CREATE TABLE IF NOT EXISTS document_revisions (
	id UUID NOT NULL,
	document_id UUID NOT NULL,
	source_version_id UUID NOT NULL,
	parent_revision_id UUID,
	preview_pdf_asset_id UUID,
	schema_version INTEGER NOT NULL,
	reason VARCHAR(16) NOT NULL,
	processing_state VARCHAR(16) NOT NULL,
	publication_state VARCHAR(16) NOT NULL,
	reader_version VARCHAR(64) NOT NULL,
	config_hash VARCHAR(64) NOT NULL,
	report JSON NOT NULL,
	index_ready BOOLEAN NOT NULL,
	created_by UUID NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE NOT NULL,
	published_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_revision_document UNIQUE (document_id, id),
	CONSTRAINT uq_revision_source UNIQUE (id, source_version_id),
	CONSTRAINT fk_revision_source FOREIGN KEY(document_id, source_version_id) REFERENCES document_sources (document_id, id),
	CONSTRAINT fk_revision_parent FOREIGN KEY(document_id, parent_revision_id) REFERENCES document_revisions (document_id, id),
	CONSTRAINT ck_revision_processing CHECK (processing_state IN ('queued','processing','ready','partial','failed')),
	CONSTRAINT ck_revision_publication CHECK (publication_state IN ('draft','published','superseded')),
	CONSTRAINT ck_revision_reason CHECK (reason IN ('upload','reparse','teacher_edit')),
	FOREIGN KEY(document_id) REFERENCES documents (id),
	FOREIGN KEY(created_by) REFERENCES users (id)
);
-- statement
CREATE INDEX IF NOT EXISTS ix_document_revisions_document_id ON document_revisions (document_id);
-- statement
CREATE TABLE IF NOT EXISTS document_assets (
	id UUID NOT NULL,
	source_version_id UUID NOT NULL,
	storage_key VARCHAR(1024) NOT NULL,
	sha256 VARCHAR(64) NOT NULL,
	mime VARCHAR(128) NOT NULL,
	width INTEGER,
	height INTEGER,
	kind VARCHAR(32) NOT NULL,
	access_class VARCHAR(32) NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_asset_source UNIQUE (source_version_id, id),
	CONSTRAINT ck_asset_access CHECK (access_class IN ('teacher_original','student_visible')),
	CONSTRAINT ck_asset_kind CHECK (kind IN ('embedded_image','figure_crop','page_preview','pdf_preview')),
	FOREIGN KEY(source_version_id) REFERENCES document_sources (id),
	UNIQUE (storage_key)
);
-- statement
CREATE INDEX IF NOT EXISTS ix_document_assets_source_version_id ON document_assets (source_version_id);
-- statement
CREATE TABLE IF NOT EXISTS document_pages (
	revision_id UUID NOT NULL,
	page_id UUID NOT NULL,
	source_version_id UUID NOT NULL,
	order_index INTEGER NOT NULL,
	source_slide_no INTEGER NOT NULL,
	preview_page_no INTEGER NOT NULL,
	title TEXT,
	state VARCHAR(16) NOT NULL,
	issues JSON NOT NULL,
	width FLOAT,
	height FLOAT,
	preview_asset_id UUID,
	PRIMARY KEY (revision_id, page_id),
	CONSTRAINT uq_page_order UNIQUE (revision_id, order_index),
	CONSTRAINT uq_page_source UNIQUE (revision_id, page_id, source_version_id),
	CONSTRAINT fk_page_revision FOREIGN KEY(revision_id, source_version_id) REFERENCES document_revisions (id, source_version_id),
	CONSTRAINT fk_page_preview FOREIGN KEY(source_version_id, preview_asset_id) REFERENCES document_assets (source_version_id, id),
	CONSTRAINT ck_page_order CHECK (order_index >= 0 AND source_slide_no > 0 AND preview_page_no > 0),
	CONSTRAINT ck_page_state CHECK (state IN ('usable','partial','unavailable'))
);
-- statement
CREATE TABLE IF NOT EXISTS document_blocks (
	revision_id UUID NOT NULL,
	block_id UUID NOT NULL,
	source_version_id UUID NOT NULL,
	page_id UUID NOT NULL,
	order_index INTEGER NOT NULL,
	kind VARCHAR(16) NOT NULL,
	content TEXT NOT NULL,
	content_format VARCHAR(16) NOT NULL,
	source_text TEXT,
	source_ref JSON NOT NULL,
	section_path JSON NOT NULL,
	content_description TEXT,
	context_description TEXT,
	description_origin VARCHAR(16) NOT NULL,
	source_completeness VARCHAR(16) NOT NULL,
	index_state VARCHAR(16) NOT NULL,
	issues JSON NOT NULL,
	edited_by_teacher BOOLEAN NOT NULL,
	PRIMARY KEY (revision_id, block_id),
	CONSTRAINT uq_block_order UNIQUE (revision_id, page_id, order_index),
	CONSTRAINT uq_block_source UNIQUE (revision_id, block_id, source_version_id),
	CONSTRAINT fk_block_page FOREIGN KEY(revision_id, page_id, source_version_id) REFERENCES document_pages (revision_id, page_id, source_version_id),
	CONSTRAINT ck_block_kind CHECK (kind IN ('heading','text','code','formula','table','figure','navigation')),
	CONSTRAINT ck_block_format CHECK (content_format IN ('plain','markdown','latex')),
	CONSTRAINT ck_block_completeness CHECK (source_completeness IN ('complete','incomplete','unknown')),
	CONSTRAINT ck_block_index CHECK (index_state IN ('searchable','needs_review','excluded')),
	CONSTRAINT ck_block_origin CHECK (description_origin IN ('none','model','teacher')),
	CONSTRAINT ck_block_order CHECK (order_index >= 0)
);
-- statement
CREATE TABLE IF NOT EXISTS document_block_assets (
	revision_id UUID NOT NULL,
	block_id UUID NOT NULL,
	asset_id UUID NOT NULL,
	source_version_id UUID NOT NULL,
	role VARCHAR(16) NOT NULL,
	bbox JSON,
	PRIMARY KEY (revision_id, block_id, asset_id),
	CONSTRAINT fk_block_asset_block FOREIGN KEY(revision_id, block_id, source_version_id) REFERENCES document_blocks (revision_id, block_id, source_version_id),
	CONSTRAINT fk_block_asset_asset FOREIGN KEY(source_version_id, asset_id) REFERENCES document_assets (source_version_id, id),
	CONSTRAINT ck_block_asset_role CHECK (role IN ('primary','preview'))
);
-- statement
CREATE TABLE IF NOT EXISTS document_block_links (
	revision_id UUID NOT NULL,
	from_block_id UUID NOT NULL,
	to_block_id UUID NOT NULL,
	relation VARCHAR(16) NOT NULL,
	basis VARCHAR(24) NOT NULL,
	source_ref JSON NOT NULL,
	PRIMARY KEY (revision_id, from_block_id, to_block_id, relation),
	CONSTRAINT fk_link_from FOREIGN KEY(revision_id, from_block_id) REFERENCES document_blocks (revision_id, block_id),
	CONSTRAINT fk_link_to FOREIGN KEY(revision_id, to_block_id) REFERENCES document_blocks (revision_id, block_id),
	CONSTRAINT ck_link_basis CHECK (relation IN ('continues','explains') AND basis IN ('explicit_source','teacher')),
	CONSTRAINT ck_link_distinct CHECK (from_block_id <> to_block_id)
);
-- statement
CREATE TABLE IF NOT EXISTS document_cleanup_jobs (
	document_id UUID NOT NULL,
	state VARCHAR(16) NOT NULL,
	attempts INTEGER NOT NULL,
	next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL,
	last_error_code VARCHAR(64),
	remaining_storage_keys JSON NOT NULL,
	PRIMARY KEY (document_id),
	CONSTRAINT ck_cleanup_state CHECK (state IN ('pending','failed','complete')),
	FOREIGN KEY(document_id) REFERENCES documents (id)
);
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_document_generation' AND conrelid='documents'::regclass) THEN ALTER TABLE documents ADD CONSTRAINT ck_document_generation CHECK (generation >= 0); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_document_lifecycle' AND conrelid='documents'::regclass) THEN ALTER TABLE documents ADD CONSTRAINT ck_document_lifecycle CHECK (lifecycle IN ('active','archived','deleted')); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_document_current_revision' AND conrelid='documents'::regclass) THEN ALTER TABLE documents ADD CONSTRAINT fk_document_current_revision FOREIGN KEY(id, current_revision_id) REFERENCES document_revisions (document_id, id); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_chunk_range' AND conrelid='doc_chunks'::regclass) THEN ALTER TABLE doc_chunks ADD CONSTRAINT ck_chunk_range CHECK ((revision_id IS NULL AND block_id IS NULL) OR (revision_id IS NOT NULL AND block_id IS NOT NULL AND content_start IS NOT NULL AND content_end IS NOT NULL AND part_index IS NOT NULL AND part_count IS NOT NULL AND content_start >= 0 AND content_end > content_start AND part_index >= 0 AND part_count > part_index)); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_chunk_block' AND conrelid='doc_chunks'::regclass) THEN ALTER TABLE doc_chunks ADD CONSTRAINT fk_chunk_block FOREIGN KEY(revision_id, block_id) REFERENCES document_blocks (revision_id, block_id); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_chunk_revision' AND conrelid='doc_chunks'::regclass) THEN ALTER TABLE doc_chunks ADD CONSTRAINT fk_chunk_revision FOREIGN KEY(document_id, revision_id) REFERENCES document_revisions (document_id, id); END IF; END $$;
-- statement
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_revision_preview' AND conrelid='document_revisions'::regclass) THEN ALTER TABLE document_revisions ADD CONSTRAINT fk_revision_preview FOREIGN KEY(source_version_id, preview_pdf_asset_id) REFERENCES document_assets (source_version_id, id); END IF; END $$;
-- statement
CREATE INDEX IF NOT EXISTS ix_doc_chunks_revision_id ON doc_chunks (revision_id);
-- statement
CREATE OR REPLACE FUNCTION protect_document_source() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE docid uuid; life text; object_key text;
BEGIN
    IF TG_OP = 'UPDATE' THEN RAISE EXCEPTION 'immutable_source_or_asset'; END IF;
    IF TG_TABLE_NAME = 'document_sources' THEN
        IF TG_OP = 'DELETE' THEN docid := OLD.document_id; ELSE docid := NEW.document_id; END IF;
    ELSE
        SELECT document_id INTO docid FROM document_sources
          WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.source_version_id ELSE NEW.source_version_id END;
    END IF;
    SELECT lifecycle INTO life FROM documents WHERE id = docid FOR UPDATE;
    IF TG_OP = 'DELETE' THEN
        IF life IS DISTINCT FROM 'deleted' THEN RAISE EXCEPTION 'source_requires_document_deletion'; END IF;
        RETURN OLD;
    END IF;
    IF life IS DISTINCT FROM 'active' THEN RAISE EXCEPTION 'source_unavailable'; END IF;
    object_key := NEW.storage_key;
    IF object_key NOT LIKE docid::text || '/%' OR object_key LIKE '%/../%' THEN
        RAISE EXCEPTION 'invalid_storage_key';
    END IF;
    IF TG_TABLE_NAME = 'document_assets' AND EXISTS (SELECT 1 FROM document_sources WHERE storage_key=object_key) THEN
        RAISE EXCEPTION 'storage_key_already_registered';
    END IF;
    IF TG_TABLE_NAME = 'document_sources' AND EXISTS (SELECT 1 FROM document_assets WHERE storage_key=object_key) THEN
        RAISE EXCEPTION 'storage_key_already_registered';
    END IF;
    RETURN NEW;
END $$;
-- statement
DROP TRIGGER IF EXISTS immutable_document_source ON document_sources;
-- statement
CREATE TRIGGER immutable_document_source BEFORE INSERT OR UPDATE OR DELETE ON document_sources FOR EACH ROW EXECUTE FUNCTION protect_document_source();
-- statement
DROP TRIGGER IF EXISTS immutable_document_asset ON document_assets;
-- statement
CREATE TRIGGER immutable_document_asset BEFORE INSERT OR UPDATE OR DELETE ON document_assets FOR EACH ROW EXECUTE FUNCTION protect_document_source();
-- statement
CREATE OR REPLACE FUNCTION protect_document_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE rid uuid; docid uuid; pub text; life text;
BEGIN
    IF TG_OP = 'DELETE' THEN rid := OLD.revision_id; ELSE rid := NEW.revision_id; END IF;
    IF TG_OP = 'UPDATE' AND OLD.revision_id IS DISTINCT FROM NEW.revision_id THEN
        RAISE EXCEPTION 'immutable_revision_identity';
    END IF;
    IF rid IS NULL THEN RETURN COALESCE(NEW, OLD); END IF;
    SELECT document_id INTO docid FROM document_revisions WHERE id = rid;
    SELECT lifecycle INTO life FROM documents WHERE id = docid FOR UPDATE;
    SELECT publication_state INTO pub FROM document_revisions WHERE id = rid;
    IF TG_OP = 'DELETE' AND life = 'deleted' THEN RETURN OLD; END IF;
    IF pub IS DISTINCT FROM 'draft' OR life IS DISTINCT FROM 'active' THEN
        RAISE EXCEPTION 'immutable_revision';
    END IF;
    RETURN COALESCE(NEW, OLD);
END $$;
-- statement
DO $$ DECLARE t text; BEGIN
    FOREACH t IN ARRAY ARRAY['document_pages','document_blocks','document_block_assets','document_block_links','doc_chunks'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS immutable_snapshot ON %I', t);
        EXECUTE format('CREATE TRIGGER immutable_snapshot BEFORE INSERT OR UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION protect_document_snapshot()', t);
    END LOOP;
END $$;
-- statement
CREATE OR REPLACE FUNCTION protect_document_revision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE life text;
BEGIN
    SELECT lifecycle INTO life FROM documents WHERE id = COALESCE(NEW.document_id, OLD.document_id) FOR UPDATE;
    IF TG_OP = 'DELETE' THEN
        IF life <> 'deleted' THEN RAISE EXCEPTION 'revision_requires_document_deletion'; END IF;
        RETURN OLD;
    END IF;
    IF life <> 'active' THEN RAISE EXCEPTION 'source_unavailable'; END IF;
    IF TG_OP = 'UPDATE' AND OLD.publication_state <> 'draft' THEN
        IF OLD.publication_state <> 'published' OR NEW.publication_state <> 'superseded'
           OR (to_jsonb(NEW) - 'publication_state') IS DISTINCT FROM (to_jsonb(OLD) - 'publication_state') THEN
            RAISE EXCEPTION 'immutable_revision';
        END IF;
    END IF;
    RETURN NEW;
END $$;
-- statement
DROP TRIGGER IF EXISTS immutable_revision ON document_revisions;
-- statement
CREATE TRIGGER immutable_revision BEFORE INSERT OR UPDATE OR DELETE ON document_revisions FOR EACH ROW EXECUTE FUNCTION protect_document_revision();
