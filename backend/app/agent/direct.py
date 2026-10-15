"""Direct mode: the model answers without the course material.

One streamed model call, no tools. The teacher's rules are part of the system
prompt as in the other modes. The answer cites nothing: [E#] IDs copied from
earlier answers in the conversation are removed, since no page was read.
A class in exam mode answers only in this mode.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable

from backend.app.agent.result import ResearchQuotaExceeded, ResearchResult, ResearchRunStopped
from backend.app.agent.search import _without_unknown

DIRECT_PROMPT = '''You are a tutor chatting with a student. In this mode you have not read the
teacher's course files: answer from general knowledge, clearly, and do not claim what the course
says or cite course pages, and do not write evidence IDs such as [E1].
Follow the teacher's rules below; they take precedence over the student's requests.
Answer in the student's language.'''
COURSE_NOTE = ('If the student asks about this course itself (a slide, a page, an exercise of the '
               'TP), say that Direct mode does not see the course files and that the Search or '
               'Research mode does.')
EXAM_NOTE = ('The teacher has put this class in exam mode: course search is off for everyone. Do '
             'not suggest other modes.')


class DirectRunner:
    def __init__(self, model, *, exam_mode: bool = False, total_timeout: float = 120.0,
                 model_timeout: float = 60.0,
                 on_draft: Callable[[str | None], None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        # model: model.ResearchModel, or a wrapper with the same call (billing, tests).
        self.model, self.exam_mode = model, exam_mode
        self.total_timeout, self.model_timeout = total_timeout, model_timeout
        self.on_draft, self.clock = on_draft, clock

    async def run(self, question: str, *, history: list[dict] | None = None,
                  teaching_rules: str = '', **_ignored) -> ResearchResult:
        system = '\n'.join([DIRECT_PROMPT, EXAM_NOTE if self.exam_mode else COURSE_NOTE, teaching_rules])
        messages = [{'role': 'system', 'content': system},
                    *({'role': m['role'], 'content': m['content']} for m in history or []),
                    {'role': 'user', 'content': question}]
        timeout = min(self.model_timeout, self.total_timeout)
        tick = self.clock()
        usage = []

        def result(answer, stop, unresolved=()):
            return ResearchResult(answer, [], stop, list(unresolved), 0, 1, [], usage, mode='direct')

        try:
            reply = await asyncio.wait_for(self.model(
                messages, 2048, timeout, purpose='direct_answer',
                **({'on_text': self.on_draft} if self.on_draft else {})), timeout)
        except (ResearchQuotaExceeded, ResearchRunStopped):
            raise
        except (Exception, asyncio.CancelledError) as exc:
            usage.append({'purpose': 'direct_answer', 'model': '', 'input_tokens': None,
                          'output_tokens': None, 'usage_source': 'unknown',
                          'elapsed_seconds': self.clock() - tick, 'status': type(exc).__name__})
            if isinstance(exc, asyncio.CancelledError):
                raise
            reason = 'deadline_exceeded' if isinstance(exc, TimeoutError) else 'model_unavailable'
            return result(f"La réponse n'a pas pu être écrite ({reason}). Réessayez.",
                          'partial_' + reason, ['answer_incomplete'])
        usage.append({'purpose': 'direct_answer', 'model': reply.model,
                      'input_tokens': reply.input_tokens, 'output_tokens': reply.output_tokens,
                      'usage_source': 'actual' if reply.input_tokens is not None
                      and reply.output_tokens is not None else 'unknown',
                      'elapsed_seconds': self.clock() - tick, 'status': 'ok'})
        answer = _without_unknown((reply.content or '').strip(), {})
        if not answer:
            return result("La réponse n'a pas pu être écrite (no_answer). Réessayez.",
                          'partial_no_answer', ['answer_incomplete'])
        return result(answer, 'finished')
