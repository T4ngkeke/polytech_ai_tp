-- M5-B: drop two tables that nothing reads any more. `rules` was copied into
-- rule_items by M4-J (2026-11-10); `session_read_state` (M4-C) held the
-- follow-up source selection, unused since 2026-10-12. The old rule texts go
-- with `rules`: back the database up first. Apply after M5-A.
-- statement
DROP TABLE IF EXISTS session_read_state;
-- statement
DROP TABLE IF EXISTS rules;
-- statement
DROP TYPE IF EXISTS rulelevel;
