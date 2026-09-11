-- Apply before starting code that uses page provenance/partial status.
ALTER TYPE documentstatus ADD VALUE IF NOT EXISTS 'partially_indexed';
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS source_text TEXT;
ALTER TABLE doc_chunks ADD COLUMN IF NOT EXISTS source_ref JSON;
