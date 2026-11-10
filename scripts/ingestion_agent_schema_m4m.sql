-- M4-M additive: a rule remembers where it came from (a ready-made rule
-- 'b:<key>', a teacher's saved rule 'm:<id>', or nothing), so the button that
-- added it can remove it again. Rules whose text is a ready-made text get its
-- key; the rest stay without a source. Apply after M4-L.
-- statement
ALTER TABLE rule_items ADD COLUMN IF NOT EXISTS source VARCHAR(64);
-- statement
UPDATE rule_items SET source = b.source FROM (VALUES
  ('b:guide', 'Do not give the full solution. Guide the student with questions and hints so that they find the answer themselves.'),
  ('b:short', 'Keep answers short: a few sentences, unless the student asks for more detail.'),
  ('b:hints', 'Do not write complete programs. Give hints, pseudo-code or small fragments only.'),
  ('b:ask-tried', 'Before helping with an exercise, ask the student what they have already tried.'),
  ('b:steps', 'Explain step by step, with one small example.'),
  ('b:check', 'End an explanation with one short question that checks the student understood.'),
  ('b:point-course', 'When the course covers the topic, say which file and page the student should read.'),
  ('b:vocabulary', 'Use the notation and the vocabulary of the course documents.'),
  ('b:stay-course', 'Only answer questions about this course. Politely decline other topics.'),
  ('b:french', 'Always answer in French.')
) AS b(source, text)
WHERE rule_items.source IS NULL AND rule_items.text = b.text;
