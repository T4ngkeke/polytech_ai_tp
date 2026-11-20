-- M4-X additive: the web sources an answer cites ([W#] from the web search
-- tool ported from Open WebUI). Apply after M4-W.
-- statement
ALTER TABLE messages ADD COLUMN IF NOT EXISTS web_sources JSON;
