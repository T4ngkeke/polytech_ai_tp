-- M4-O additive: an admin resets a password to the default one; the user
-- must then choose a new password before using the app. Apply after M4-N.
-- statement
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;
