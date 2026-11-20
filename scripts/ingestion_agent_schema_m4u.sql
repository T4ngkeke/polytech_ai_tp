-- M4-U additive: what the assistant remembers about a student, per class,
-- and the student's switch to turn memory off. Apply after M4-T.
-- statement
CREATE TABLE IF NOT EXISTS student_memories (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  category VARCHAR(64),
  source_message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
  embedding vector(1024),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- statement
CREATE INDEX IF NOT EXISTS ix_student_memories_user_class ON student_memories (user_id, class_id);
-- statement
ALTER TABLE users ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT true;
