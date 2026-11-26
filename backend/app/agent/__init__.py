"""The research agent of the student chat (README.md in this folder).

  runner.py        the loop: one model researches and answers in one conversation
  course_tools.py  the course tools as the model sees them: handles, compact text,
                   tool budget and verified evidence
  tools.py         access checks and reading of the published course PDFs
  evidence.py      evidence IDs (E1, E2, ...) and their citations
  web_tools.py, code_tools.py, task_tools.py
                   web search, Python in the browser, the task checklist
  history.py, compaction.py, memory.py, attachments.py, language.py
                   what goes into the prompt besides the question
  follow_ups.py, visuals.py
                   suggested next questions; diagrams and charts in answers
  model.py         the model client
  result.py        the run result and its stop exceptions

Persistence and billing are in services/research_*.py.
"""
