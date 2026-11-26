-- M5-C: when exam mode was turned on (classes.exam_mode_since). During an
-- exam, students do not see the conversations they started before it; their
-- teacher still does. A class already in exam mode counts from now. Apply
-- after M5-B.
-- statement
ALTER TABLE classes ADD COLUMN IF NOT EXISTS exam_mode_since TIMESTAMPTZ;
-- statement
UPDATE classes SET exam_mode_since = now() WHERE exam_mode AND exam_mode_since IS NULL;
