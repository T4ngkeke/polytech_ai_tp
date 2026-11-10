-- M4-K additive: in exam mode a class can keep some files open (a whitelist).
-- Students may search and open only the files with exam_allowed; none by default.
-- Apply after M4-J.
-- statement
ALTER TABLE documents ADD COLUMN IF NOT EXISTS exam_allowed BOOLEAN NOT NULL DEFAULT false;
