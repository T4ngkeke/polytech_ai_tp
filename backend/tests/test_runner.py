"""The research runner: one model researches and answers in one conversation."""
import json
from uuid import uuid4

import pytest

from backend.app.agent.course_tools import CourseTools
from backend.app.agent.model import ModelReply
from backend.app.agent.result import ResearchRunStopped
from backend.app.agent.runner import (
    COURSE_TOOLS, FINISH_TURN, ResearchRunner, partial_answer, system_prompt,
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


def test_course_tools_have_plain_names_and_no_finish():
    assert [t['function']['name'] for t in COURSE_TOOLS] == [
        'grep', 'navigate', 'open', 'read', 'search', 'view_image']


def test_prompt_names_the_run_limits():
    # The default limits give the 2026-10 benchmark text unchanged.
    assert 'at most eight course tool calls and two visual calls.' in system_prompt(8, 2)
    assert 'at most twenty course tool calls and four visual calls.' in system_prompt(20, 4)
    assert 'at most 30 course tool calls' in system_prompt(30, 2)


def test_research_limits_from_admin_values():
    from backend.app.services.research_service import ResearchLimits
    assert ResearchLimits.from_config({}) == ResearchLimits()
    limits = ResearchLimits.from_config({
        'AGENT_MAX_TOOL_CALLS': ' 20 ', 'AGENT_MAX_VISUAL_CALLS': 'four',
        'AGENT_RESEARCH_SECONDS': '300', 'AGENT_TOTAL_SECONDS': '120',
        'AGENT_MAX_INPUT_TOKENS': '999999'})
    assert (limits.tool_calls, limits.visual_calls, limits.input_tokens) == (20, 2, 12000)
    # Research time never exceeds the total time.
    assert (limits.research_seconds, limits.total_seconds) == (120, 120)


@pytest.mark.asyncio
async def test_prompt_uses_the_course_tools_limits():
    model = Script('Bonjour.')
    await ResearchRunner(CourseTools(ViewFixture(), max_tool_calls=12, max_visual_calls=3),
                         model, max_tool_calls=12).run('Bonjour')
    assert 'at most twelve course tool calls and three visual calls.' in model.seen[0][0]['content']


@pytest.mark.asyncio
async def test_answer_is_written_in_the_research_conversation():
    model = Script([('search', {'query': 'produit cartésien'})], [('read', {'page': 'd1.p10'})],
                   'Vin3 = product(Vin1, Années) [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run(
        'Produit cartésien ?', research_note='NOTE')
    assert result.stop_reason == 'finished' and result.answer.endswith('[E1].')
    assert [c['evidence_id'] for c in result.citations] == ['E1']
    assert result.model_calls == 3 and result.tool_calls == 2
    last = model.seen[-1]
    assert last[0]['content'].startswith(system_prompt(8, 2)) and last[0]['content'].endswith('NOTE')
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
    # The system message is unchanged, so the model server can reuse its prefix.
    assert model.seen[-1][0] == model.seen[0][0]
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
    assert tool_texts[0].startswith('error: invalid_arguments (')
    assert tool_texts[1] == 'error: unknown_tool (nope)'
    assert result.answer == 'Réponse générale.' and result.citations == []
    # Both refused calls take a tool slot, the unknown tool name included.
    assert result.tool_calls == 2


@pytest.mark.asyncio
async def test_history_is_sent_as_turns_with_their_evidence_ids():
    model = Script('Oui.')
    history = [{'role': 'user', 'content': 'Produit cartésien ?'},
               {'role': 'assistant', 'content': 'Voir la page 10 [E1, E2].'}]
    await ResearchRunner(CourseTools(ViewFixture()), model).run('Et la page 11 ?', history=history)
    assert model.seen[0][1:] == [
        {'role': 'user', 'content': 'Produit cartésien ?'},
        {'role': 'assistant', 'content': 'Voir la page 10 [E1, E2].'},
        {'role': 'user', 'content': 'Et la page 11 ?'}]


@pytest.mark.asyncio
async def test_a_follow_up_may_cite_an_earlier_answer_without_new_research():
    earlier = {'E2': {'evidence_id': 'E2', 'document_id': str(uuid4()), 'revision_id': str(uuid4()),
                      'page_id': str(uuid4()), 'pdf_page_no': 10, 'block_id': None, 'range': None,
                      'asset_id': None, 'origin': 'native'}}
    fixture = ViewFixture()
    async def readable(document_id):
        return None
    fixture._revision = readable
    model = Script('Comme expliqué [E2].')
    result = await ResearchRunner(CourseTools(fixture, first_evidence=3), model).run(
        'Et donc ?', history=[{'role': 'user', 'content': 'Q ?'},
                              {'role': 'assistant', 'content': 'Voir [E2].'}],
        earlier_citations=earlier)
    assert result.stop_reason == 'finished' and result.citations == [earlier['E2']]


def test_each_tool_call_is_logged(caplog):
    import asyncio
    import logging
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [E1].')
    with caplog.at_level(logging.INFO, logger='backend.app.agent.runner'):
        asyncio.run(ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?'))
    lines = [r.getMessage() for r in caplog.records if 'research run=' in r.getMessage()]
    assert len(lines) == 1 and 'tool=read' in lines[0] and 'ok=True' in lines[0]


@pytest.mark.asyncio
async def test_a_later_run_continues_the_numbering_and_corrects_old_ids():
    # The session's earlier answer cited E1-E2; this run starts at E3.
    model = Script([('read', {'page': 'd1.p10'})], 'Encore [E1].', 'Encore [E3].')
    result = await ResearchRunner(CourseTools(ViewFixture(), first_evidence=3), model).run(
        'Et la page 10 ?', history=[{'role': 'user', 'content': 'Q ?'},
                                    {'role': 'assistant', 'content': 'Voir [E1, E2].'}])
    assert 'unknown evidence IDs' in model.seen[-1][-1]['content'] and 'E3' in model.seen[-1][-1]['content']
    assert result.answer == 'Encore [E3].' and [c['evidence_id'] for c in result.citations] == ['E3']


@pytest.mark.asyncio
async def test_several_ids_in_one_bracket_are_all_cited():
    model = Script([('read', {'page': 'd1.p10'}), ('read', {'page': 'd1.p9'})], 'Réponse [E1, E2].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.stop_reason == 'finished'
    assert [c['evidence_id'] for c in result.citations] == ['E1', 'E2']


@pytest.mark.asyncio
async def test_a_lab_without_documents_gets_no_tools():
    class EmptyLab(ViewFixture):
        async def page_index(self):
            return {}
    model = Script('Ce TP n\'a pas encore de documents de cours.')
    result = await ResearchRunner(CourseTools(EmptyLab()), model).run('Automate ?')
    assert model.with_tools == [False] and result.tool_calls == 0
    assert result.stop_reason == 'no_documents' and result.citations == []
    assert 'no course documents yet' in model.seen[0][-1]['content']


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
