-- M4-T additive: versions of the last turn. A new question names the first
-- question of its group (version_of); the replaced question and answer get
-- superseded_at. Apply after M4-S.
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS version_of UUID REFERENCES messages(id) ON DELETE SET NULL;
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS superseded_at TIMESTAMPTZ;
