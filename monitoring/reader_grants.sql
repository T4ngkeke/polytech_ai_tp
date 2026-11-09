-- What the Grafana login (grafana_reader) may read: these columns and no
-- others. No user names, e-mails, passwords, answers or course text. From
-- users only the role and the token quotas (to show who is at a limit,
-- without names).
-- Applied by scripts/monitoring_reader.py, which creates the login first.
-- Column grants do not block migrations: a dropped column loses its grant.

-- statement
GRANT USAGE ON SCHEMA public TO grafana_reader;
-- statement
GRANT SELECT (id, session_id, question, status, stop_reason, started_at, finished_at,
              assistant_message_id, mode) ON agent_runs TO grafana_reader;
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
GRANT SELECT (id, name, is_deleted) ON classes TO grafana_reader;
-- statement
GRANT SELECT (id, filename, lifecycle, page_count) ON documents TO grafana_reader;
-- statement
GRANT SELECT (id, document_id, status, task_kind, attempts, error_message, created_at,
              updated_at, revision_id) ON ingestion_jobs TO grafana_reader;
-- statement
GRANT SELECT (id, document_id, created_at, parse_started_at, parse_finished_at)
    ON document_revisions TO grafana_reader;
-- statement
GRANT SELECT (id, role, daily_token_quota, weekly_token_quota) ON users TO grafana_reader;
-- statement
GRANT SELECT (user_id, date, tokens_used) ON usage_stats TO grafana_reader;
-- statement
GRANT SELECT (user_id, date, kind, tokens, created_at) ON quota_grants TO grafana_reader;
-- statement
GRANT SELECT (user_id, date, resolved_at) ON quota_reset_requests TO grafana_reader;
-- statement
GRANT SELECT (user_id, lab_id, reason, created_at) ON chat_refusals TO grafana_reader;
-- statement
GRANT SELECT (class_id, student_id) ON class_students TO grafana_reader;
