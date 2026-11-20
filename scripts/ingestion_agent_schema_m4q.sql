-- M4-Q additive: the notification bell. One row per message for one user;
-- payload holds the names and ids the page needs. Apply after M4-P.
-- statement
CREATE TABLE IF NOT EXISTS notifications (
  id UUID PRIMARY KEY,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind VARCHAR(32) NOT NULL,
  payload JSON NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  read_at TIMESTAMPTZ
);
-- statement
CREATE INDEX IF NOT EXISTS ix_notifications_user_created ON notifications (user_id, created_at);
