-- M4-E additive: a research answer that is a question back to the student
-- (the ask_student tool) keeps its options. Apply explicitly after M4-C.
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS clarification JSON;
