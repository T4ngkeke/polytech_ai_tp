-- M4-V additive: how an answer used the course (cited, not_found, none), for
-- the labels "not from the course" and "not found in the course". Apply after M4-U.
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS course_use VARCHAR(16);
