-- M4-Y additive: the Python runs of an answer (execute_code, ported from
-- Open WebUI; the code runs in the student's browser). Apply after M4-X.
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS code_runs JSON;
