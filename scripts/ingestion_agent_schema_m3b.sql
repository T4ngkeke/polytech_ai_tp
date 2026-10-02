-- M3-B: a parsed PDF revision reuses its parse job as an index task.
-- statement
ALTER TABLE ingestion_jobs DROP CONSTRAINT IF EXISTS ck_job_binding;
-- statement
ALTER TABLE ingestion_jobs ADD CONSTRAINT ck_job_binding CHECK
  ((task_kind IS NULL AND source_version_id IS NULL AND revision_id IS NULL)
   OR (task_kind IN ('odp_parse','pdf_parse','pdf_index') AND source_version_id IS NOT NULL AND revision_id IS NOT NULL));
