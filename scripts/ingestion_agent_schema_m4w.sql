-- M4-W additive: the task checklist the model kept while answering
-- (ported from Open WebUI's create_tasks / update_task). Apply after M4-V.
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS tasks JSON;
