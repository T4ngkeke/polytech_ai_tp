-- What the Grafana login (grafana_reader) may read: these columns and no
-- others. No user names, e-mails, passwords, answers or course text.
-- Applied by scripts/monitoring_reader.py, which creates the login first.
-- Column grants do not block migrations: a dropped column loses its grant.

-- statement
GRANT USAGE ON SCHEMA public TO grafana_reader;
-- statement
GRANT SELECT (id, session_id, question, status, stop_reason, started_at, finished_at,
              assistant_message_id) ON agent_runs TO grafana_reader;
-- statement
GRANT SELECT (id, answer_mode, feedback, billed_tokens, created_at) ON messages TO grafana_reader;
-- statement
GRANT SELECT (run_id, purpose, model, input_tokens, output_tokens, billed_tokens,
              elapsed_seconds, status, created_at) ON model_call_usage TO grafana_reader;
-- statement
GRANT SELECT (id, lab_id, is_test) ON sessions TO grafana_reader;
-- statement
GRANT SELECT (id, name, class_id) ON labs TO grafana_reader;
-- statement
GRANT SELECT (id, name) ON classes TO grafana_reader;
-- statement
GRANT SELECT (id, filename, lifecycle) ON documents TO grafana_reader;
-- statement
GRANT SELECT (id, document_id, status, task_kind, attempts, error_message, created_at,
              updated_at) ON ingestion_jobs TO grafana_reader;
