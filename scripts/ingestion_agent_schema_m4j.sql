-- M4-J: rules become a list (rule_items), one row per rule, for a class, a lab
-- or a student in one class. Each existing text of `rules` becomes one item;
-- a student rule (which applied in every class) becomes one item per class of
-- the student. `rules` stays, no longer read. Apply after M4-I.
-- statement
CREATE TABLE IF NOT EXISTS rule_items (
  id UUID PRIMARY KEY,
  level VARCHAR(8) NOT NULL CONSTRAINT ck_rule_item_level CHECK (level IN ('class','lab','student')),
  target_id UUID NOT NULL,
  class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT true,
  position INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
-- statement
CREATE INDEX IF NOT EXISTS ix_rule_items_target ON rule_items (level, target_id);
-- statement
CREATE INDEX IF NOT EXISTS ix_rule_items_class_id ON rule_items (class_id);
-- statement
INSERT INTO rule_items (id, level, target_id, class_id, text, is_active, position, created_at, updated_at)
SELECT gen_random_uuid(), 'class', r.target_id, c.id, r.rules_text, r.is_active, 0, now(), now()
FROM rules r JOIN classes c ON c.id = r.target_id
WHERE r.level::text = 'class_' AND btrim(r.rules_text) <> ''
  AND NOT EXISTS (SELECT 1 FROM rule_items i WHERE i.level = 'class' AND i.target_id = r.target_id);
-- statement
INSERT INTO rule_items (id, level, target_id, class_id, text, is_active, position, created_at, updated_at)
SELECT gen_random_uuid(), 'lab', r.target_id, l.class_id, r.rules_text, r.is_active, 0, now(), now()
FROM rules r JOIN labs l ON l.id = r.target_id
WHERE r.level::text = 'lab' AND btrim(r.rules_text) <> ''
  AND NOT EXISTS (SELECT 1 FROM rule_items i WHERE i.level = 'lab' AND i.target_id = r.target_id);
-- statement
INSERT INTO rule_items (id, level, target_id, class_id, text, is_active, position, created_at, updated_at)
SELECT gen_random_uuid(), 'student', r.target_id, cs.class_id, r.rules_text, r.is_active, 0, now(), now()
FROM rules r JOIN class_students cs ON cs.student_id = r.target_id
WHERE r.level::text = 'student' AND btrim(r.rules_text) <> ''
  AND NOT EXISTS (SELECT 1 FROM rule_items i WHERE i.level = 'student' AND i.target_id = r.target_id
                  AND i.class_id = cs.class_id);
