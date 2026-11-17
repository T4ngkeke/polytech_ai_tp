"""Direct mode: one model call, no search, the TP text when there is one, the teacher's rules."""
import pytest

from backend.app.agent.direct import CONCISE_NOTE, COURSE_NOTE, DIRECT_PROMPT, EXAM_NOTE, DirectRunner
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
        return ModelReply(self.text, [], 'flash-next', 30, 8)


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
async def test_concise_comes_before_the_teacher_rules_that_may_change_it():
    model = Model('Réponse.')
    rules = '[CLASS RULES]\nRéponds en détail.'
    await DirectRunner(model, concise=True).run('Q ?', teaching_rules=rules)
    await DirectRunner(model).run('Q ?', teaching_rules=rules)
    concise, plain = (seen[0]['content'] for seen in model.seen)
    assert concise.index(CONCISE_NOTE) < concise.index(rules) and "teacher's rules" in CONCISE_NOTE
    assert CONCISE_NOTE not in plain


def test_the_product_answers_concisely():
    from backend.app.agent import direct
    assert direct.CONCISE is True


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


@pytest.mark.asyncio
async def test_the_tp_text_follows_the_rules_without_ids():
    from backend.app.agent.course_tools import CourseTools
    from backend.app.agent.direct import DIRECT_TP_PROMPT, TP_COURSE_NOTE, TP_NOTE
    from backend.tests.test_search_mode import TwoFiles
    tools = TwoFiles()
    model = Model('Le rendu se fait en binôme [E1] (page 1 du TP).')
    result = await DirectRunner(model, course_tools=CourseTools(tools)).run(
        'On fait quoi ?', teaching_rules='[LAB RULES]\n1. Pas de solution complète.')
    system = model.seen[0][0]['content']
    # A prompt that says the TP was read, not the one that says no course file was.
    assert system.startswith(DIRECT_TP_PROMPT) and DIRECT_PROMPT not in system
    assert TP_COURSE_NOTE in system and COURSE_NOTE not in system
    assert system.index('[LAB RULES]') < system.index(TP_NOTE)
    assert system.endswith(TP_NOTE + '\n\n--- [This TP] TP1.pdf, page 1 ---\nTP1 : objectifs, rendu en binôme'
                           '\n\n--- [This TP] TP1.pdf, page 2 ---\nQuestion 3 : tracer la courbe')
    # Only the TP pages are read; no search, no class course page.
    assert [name for name, _ in tools.calls] == ['read', 'read'] and 'Le langage C' not in system
    assert result.answer == 'Le rendu se fait en binôme  (page 1 du TP).' and result.citations == []
    assert result.trace == [{'event': 'tp_text', 'pages': 2, 'chars': len(system) - system.index('--- [This TP]'),
                             'in_prompt': True, 'left_out': 0}]


@pytest.mark.asyncio
async def test_without_a_tp_file_or_over_the_limit_direct_is_as_before(monkeypatch):
    from backend.app.agent import tp_text
    from backend.app.agent.course_tools import CourseTools
    from backend.tests.test_course_tools import ViewFixture
    from backend.tests.test_search_mode import TwoFiles
    model = Model('Réponse.')
    await DirectRunner(model).run('Q ?', teaching_rules='RULES')
    fixture = ViewFixture()
    await DirectRunner(model, course_tools=CourseTools(fixture)).run('Q ?', teaching_rules='RULES')
    assert model.seen[1] == model.seen[0] and fixture.calls == []
    monkeypatch.setattr(tp_text, 'TP_CHARS', 50)
    await DirectRunner(model, course_tools=CourseTools(TwoFiles())).run('Q ?', teaching_rules='RULES')
    assert model.seen[2] == model.seen[0]


@pytest.mark.asyncio
async def test_exam_mode_keeps_its_note_with_the_tp_text():
    from backend.app.agent.course_tools import CourseTools
    from backend.app.agent.direct import EXAM_FILES_NOTE, TP_COURSE_NOTE, TP_NOTE
    from backend.tests.test_search_mode import TwoFiles
    # The scope holds only the files the teacher allowed (tools.create_scope); here the TP file is one.
    model = Model('Réponse.')
    await DirectRunner(model, course_tools=CourseTools(TwoFiles()), exam_mode=True, exam_files=True).run('Q ?')
    system = model.seen[0][0]['content']
    assert EXAM_FILES_NOTE in system and TP_NOTE in system
    assert TP_COURSE_NOTE not in system and COURSE_NOTE not in system
