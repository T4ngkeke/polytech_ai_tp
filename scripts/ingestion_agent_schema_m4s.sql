-- M4-S additive: files students upload in the chat, and what a question
-- attached (a file, a course file, an earlier chat). Apply after M4-R.
-- statement
CREATE TABLE IF NOT EXISTS chat_files (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  lab_id UUID REFERENCES labs(id) ON DELETE SET NULL,
  session_id UUID REFERENCES sessions(id) ON DELETE SET NULL,
  filename VARCHAR(255) NOT NULL,
  mime VARCHAR(100) NOT NULL,
  size INTEGER NOT NULL,
  kind VARCHAR(8) NOT NULL,
  storage_key VARCHAR(512) NOT NULL,
  text TEXT,
  text_chars INTEGER NOT NULL DEFAULT 0,
  has_text BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  deleted_at TIMESTAMPTZ
);
-- statement
CREATE INDEX IF NOT EXISTS ix_chat_files_user_created ON chat_files (user_id, created_at);
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS attachments JSON;
