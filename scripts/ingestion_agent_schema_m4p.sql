-- M4-P additive: what students may use, per class (upload, web, code, deep;
-- a missing key means on) and per lab ('on' or 'off'; a missing key follows
-- the class). Apply after M4-O.
-- statement
ALTER TABLE classes ADD COLUMN IF NOT EXISTS features JSON NOT NULL DEFAULT '{}';
-- statement
ALTER TABLE labs ADD COLUMN IF NOT EXISTS feature_overrides JSON NOT NULL DEFAULT '{}';
