"""The research runner: one model researches and answers in one conversation."""
import json
from uuid import uuid4

import pytest

from backend.app.agent.course_tools import CourseTools
from backend.app.agent.model import ModelReply
from backend.app.agent.result import ResearchRunStopped
from backend.app.agent.runner import (
    COURSE_TOOLS, FINISH_TURN, SYSTEM_PROMPT, ResearchRunner, partial_answer,
)
from backend.tests.test_course_tools import ViewFixture


class Script:
    """Each step is an answer text or a list of (tool name, arguments)."""

    def __init__(self, *steps):
        self.steps, self.seen, self.with_tools, self.purposes = list(steps), [], [], []

    async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
        self.seen.append(json.loads(json.dumps(messages)))
        self.with_tools.append(tools == COURSE_TOOLS)
        self.purposes.append(purpose)
        step = self.steps.pop(0)
        if isinstance(step, str):
            return ModelReply(step, [], 'ornith', 100, 10)
        return ModelReply('', [{'id': f'c{len(self.seen)}{n}', 'name': name, 'arguments': json.dumps(args)}
                                for n, (name, args) in enumerate(step)], 'ornith', 100, 10)


def test_course_tools_keep_the_benchmark_names_and_have_no_finish():
    assert [t['function']['name'] for t in COURSE_TOOLS] == [
        'mcp__courses__' + n for n in ('grep', 'navigate', 'open', 'read', 'search', 'view_image')]


@pytest.mark.asyncio
async def test_answer_is_written_in_the_research_conversation():
    model = Script([('mcp__courses__search', {'query': 'produit cartésien'})], [('read', {'page': 'd1.p10'})],
                   'Vin3 = product(Vin1, Années) [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run(
        'Produit cartésien ?', research_note='NOTE')
    assert result.stop_reason == 'finished' and result.answer.endswith('[E1].')
    assert [c['evidence_id'] for c in result.citations] == ['E1']
    assert result.model_calls == 3 and result.tool_calls == 2
    last = model.seen[-1]
    assert last[0]['content'].startswith(SYSTEM_PROMPT) and last[0]['content'].endswith('NOTE')
    assert [m['role'] for m in last] == ['system', 'user', 'assistant', 'tool', 'assistant', 'tool']
    assert 'd1.p9.b1 text: Produit cartésien' in last[3]['content']
    assert model.with_tools == [True, True, True]


@pytest.mark.asyncio
async def test_two_calls_in_one_turn_both_run():
    model = Script([('search', {'query': 'produit cartésien'}), ('read', {'page': 'd1.p10'})],
                   'Réponse [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Produit cartésien ?')
    assert result.tool_calls == 2 and result.stop_reason == 'finished'
    assert [m['role'] for m in model.seen[-1]] == ['system', 'user', 'assistant', 'tool', 'tool']


@pytest.mark.asyncio
async def test_tools_are_withdrawn_when_the_budget_is_spent():
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model, max_tool_calls=1).run('Q ?')
    assert model.with_tools == [True, False]
    assert model.seen[-1][-1] == {'role': 'user', 'content': FINISH_TURN}
    assert result.stop_reason == 'budget_exhausted' and [c['evidence_id'] for c in result.citations] == ['E1']


@pytest.mark.asyncio
async def test_unknown_evidence_id_gets_one_correction():
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [E7].', 'Réponse [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert 'E1' in model.seen[-1][-1]['content'] and model.with_tools[-1] is False
    assert result.answer == 'Réponse [E1].' and result.stop_reason == 'finished'


@pytest.mark.asyncio
async def test_unknown_evidence_id_twice_gives_the_pages_read():
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [E7].', 'Toujours [E8].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.stop_reason == 'partial_invalid_evidence_ids' and '[E1]' in result.answer
    # The listed pages are real citations, so the student can open them.
    assert [c['evidence_id'] for c in result.citations] == ['E1']
    assert result.unresolved == ['research_incomplete']


@pytest.mark.asyncio
async def test_bad_arguments_return_an_error_to_the_model():
    model = Script([('read', {'page': 'zz'}), ('nope', {})], 'Réponse générale.')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    tool_texts = [m['content'] for m in model.seen[-1] if m['role'] == 'tool']
    assert len(tool_texts) == 2 and 'unknown_tool' in tool_texts[1]
    assert result.answer == 'Réponse générale.' and result.citations == []
    # Both refused calls take a tool slot, the unknown tool name included.
    assert result.tool_calls == 2


def test_partial_answer_lists_read_pages_once_without_claims():
    doc = str(uuid4())
    state = {'handles': {'filenames': {doc: 'Algebre.pdf'}}, 'evidence': [
        {'evidence_id': 'E1', 'document_id': doc, 'pdf_page_no': 19},
        {'evidence_id': 'E2', 'document_id': doc, 'pdf_page_no': 19},
        {'evidence_id': 'E3', 'document_id': doc, 'pdf_page_no': 20}]}
    text = partial_answer(state, 'error')
    assert text.splitlines()[1:] == ['- Algebre.pdf, page 19 [E1]', '- Algebre.pdf, page 20 [E3]']
    assert "n'a pas pu être terminée (error)" in text
    assert partial_answer({'evidence': []}, 'error') is None


@pytest.mark.asyncio
async def test_progress_phases_follow_the_tools():
    phases = []
    model = Script([('search', {'query': 'produit cartésien'})], [('read', {'page': 'd1.p9'})],
                   [('view_image', {'image': 'a1', 'question': 'Quelles tables ?'})], 'Réponse [E1].')
    await ResearchRunner(CourseTools(ViewFixture()), model, on_phase=phases.append).run('Q ?')
    assert phases == ['finding', 'reading', 'checking_image', 'checking_sources']
    assert model.purposes == ['agent'] * 4


@pytest.mark.asyncio
async def test_unreadable_calls_count_toward_the_tool_budget():
    model = Script([('nope', {})], [('read', {'page': 'd1.p10'})], 'Réponse [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model, max_tool_calls=1).run('Q ?')
    # The refused call used the only slot: the read was never offered.
    assert model.with_tools[:2] == [True, False] and result.tool_calls == 1


@pytest.mark.asyncio
async def test_revoked_access_wins_over_a_finished_answer():
    fixture = ViewFixture()
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [E1].')
    runner = ResearchRunner(CourseTools(fixture), model)

    async def revoked():
        raise LookupError('membership removed')
    fixture._check_session = revoked
    result = await runner.run('Q ?')
    assert result.stop_reason == 'access_revoked' and result.citations == []
    assert '[E1]' not in result.answer


@pytest.mark.asyncio
async def test_deadline_lists_the_pages_read_without_claiming_absence():
    now = [0.0]
    model = Script([('read', {'page': 'd1.p10'})])

    async def slow_model(*args, **kwargs):
        reply = await model(*args, **kwargs)
        now[0] += 61
        return reply
    result = await ResearchRunner(CourseTools(ViewFixture()), slow_model, clock=lambda: now[0]).run('Q ?')
    assert result.stop_reason == 'partial_deadline_exceeded'
    assert 'page 10 [E1]' in result.answer and 'pas de réponse vérifiée' in result.answer
    assert [c['evidence_id'] for c in result.citations] == ['E1']


@pytest.mark.asyncio
async def test_cancellation_during_a_model_call_stops_the_run():
    async def cancelled(*args, **kwargs):
        raise ResearchRunStopped('run_not_running')
    with pytest.raises(ResearchRunStopped):
        await ResearchRunner(CourseTools(ViewFixture()), cancelled).run('Q ?')
