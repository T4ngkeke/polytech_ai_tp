-- M4-R additive: user settings (page language, "about me") and pinned
-- chats. Apply after M4-Q.
-- statement
ALTER TABLE users ADD COLUMN IF NOT EXISTS ui_language VARCHAR(8);
-- statement
ALTER TABLE users ADD COLUMN IF NOT EXISTS about_me TEXT;
-- statement
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT false;
