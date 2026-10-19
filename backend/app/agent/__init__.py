"""The research agent of the student chat.

  runner.py        the loop: one model researches and answers in one conversation
  course_tools.py  the course tools as the model sees them: handles, compact text,
                   tool budget and verified evidence
  tools.py         access checks and reading of the published course PDFs
  evidence.py      evidence IDs (E1, E2, ...) and their citations
  model.py         the model client
  result.py        the run result and its stop exceptions
  search.py        Search mode: one search, then one answer
  direct.py        Direct mode: an answer without the course material
  history.py       the size cap on the chat history

Persistence and billing are in services/research_*.py.
"""
