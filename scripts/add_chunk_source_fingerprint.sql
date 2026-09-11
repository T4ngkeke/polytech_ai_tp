-- Existing PostgreSQL databases only; run before starting the updated app/worker.
-- Fresh databases get this nullable column from SQLAlchemy create_all.
-- No rows or teacher corrections are changed. Legacy edited rows remain NULL
-- and will require review if re-ingested, because their original text is unknown.
BEGIN;
ALTER TABLE doc_chunks
    ADD COLUMN IF NOT EXISTS source_fingerprint VARCHAR(64);
COMMIT;
