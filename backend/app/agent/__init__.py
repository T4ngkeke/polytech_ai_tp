"""The research agent of the student chat (README.md in this folder).

  runner.py     the main loop: one model researches and answers in one conversation
  tools/        what the model can call: the course tools, web search, Python,
                the task checklist, and the access checks behind them
  context/      what goes into the prompt besides the question: history, its
                summary, the student's notes, attachments, the answer language
  evidence.py   evidence IDs (E1, E2, ...) and their citations
  result.py     the run result and its stop exceptions
  model.py      the model client
  visuals.py    diagrams and charts in answers
  follow_ups.py suggested next questions under an answer

Persistence and billing are in services/research_*.py.
"""
