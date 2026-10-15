"""Direct mode: one model call, no course material, the teacher's rules."""
import pytest

from backend.app.agent.direct import COURSE_NOTE, DIRECT_PROMPT, EXAM_NOTE, DirectRunner
from backend.app.agent.model import ModelReply


class Model:
    def __init__(self, text=None, error=None):
        self.text, self.error, self.seen = text, error, []

    async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
        assert tools is None and purpose == 'direct_answer'
        self.seen.append(messages)
        if self.error:
            raise self.error
        if on_text:
            on_text(self.text)
        return ModelReply(self.text, [], 'ornith', 30, 8)


@pytest.mark.asyncio
async def test_rules_history_and_no_citations():
    model = Model('Une boucle répète [E2] un bloc.')
    result = await DirectRunner(model).run(
        'Une boucle ?', history=[{'role': 'user', 'content': 'Q'}, {'role': 'assistant', 'content': 'R [E2]'}],
        teaching_rules='[CLASS RULES]\nNe donne pas la solution complète.')
    system = model.seen[0][0]['content']
    assert system.startswith(DIRECT_PROMPT) and COURSE_NOTE in system and 'Ne donne pas' in system
    assert [m['role'] for m in model.seen[0]] == ['system', 'user', 'assistant', 'user']
    assert result.answer == 'Une boucle répète  un bloc.' and result.citations == []
    assert (result.mode, result.stop_reason, result.tool_calls) == ('direct', 'finished', 0)


@pytest.mark.asyncio
async def test_exam_mode_does_not_suggest_other_modes():
    model = Model('Réponse.')
    await DirectRunner(model, exam_mode=True).run('Q ?')
    system = model.seen[0][0]['content']
    assert EXAM_NOTE in system and COURSE_NOTE not in system


@pytest.mark.asyncio
async def test_the_answer_is_streamed():
    drafts = []
    await DirectRunner(Model('Oui.'), on_draft=drafts.append).run('Q ?')
    assert drafts == ['Oui.']


@pytest.mark.asyncio
async def test_a_failed_call_still_answers_the_student():
    result = await DirectRunner(Model(error=RuntimeError('down'))).run('Q ?')
    assert result.stop_reason == 'partial_model_unavailable' and result.answer.startswith("La réponse")
