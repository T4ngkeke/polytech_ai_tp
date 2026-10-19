-- M4-F additive: answer modes. A class in exam mode answers only in Direct
-- mode (no course search), and each answer records the mode it used
-- (direct, search, research; NULL for older answers). Apply after M4-E.
-- statement
ALTER TABLE classes ADD COLUMN IF NOT EXISTS exam_mode BOOLEAN NOT NULL DEFAULT FALSE;
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS answer_mode VARCHAR(16);
