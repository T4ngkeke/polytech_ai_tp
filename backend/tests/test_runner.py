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
        'grep', 'open', 'read', 'search', 'view_image']


def test_prompt_names_the_run_limits():
    # The default limits give the 2026-10 benchmark text unchanged.
    assert 'at most eight course tool calls and two visual calls.' in system_prompt(8, 2)
    assert 'at most twenty course tool calls and four visual calls.' in system_prompt(20, 4)
    assert 'at most 30 course tool calls' in system_prompt(30, 2)


def test_research_limits_from_admin_values():
    from backend.app.services.research_service import ResearchLimits
    assert ResearchLimits.from_config({}) == ResearchLimits()
    limits = ResearchLimits.from_config({
        'AGENT_MAX_TOOL_CALLS': ' 12 ', 'AGENT_MAX_VISUAL_CALLS': 'four',
        'AGENT_RESEARCH_SECONDS': '300', 'AGENT_TOTAL_SECONDS': '120',
        'AGENT_MAX_INPUT_TOKENS': '999999'})
    assert (limits.tool_calls, limits.visual_calls, limits.input_tokens) == (12, 4, 28000)
    # Research time never exceeds the total time.
    assert (limits.research_seconds, limits.total_seconds) == (120, 120)


@pytest.mark.asyncio
async def test_prompt_uses_the_course_tools_limits():
    model = Script('Bonjour.', 'DONE')
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
    # The lab's files with their handles, so a named page needs no guess.
    assert 'Course files of this lab' in last[0]['content'] and '\nd1 Algebre.pdf (2 pages)\n' in last[0]['content']


def test_the_catalogue_lists_files_in_handle_order_and_stops_at_the_limit():
    from backend.app.agent.course_tools import ToolView
    index = {f'id{n}': {'filename': f'Cours{n:02}.pdf', 'pages': [
        {'page_id': f'p{n}-{k}', 'pdf_page_no': k} for k in range(1, n + 2)]} for n in range(3)}
    view = ToolView(index)
    assert view.catalogue() == 'd1 Cours00.pdf (1 pages)\nd2 Cours01.pdf (2 pages)\nd3 Cours02.pdf (3 pages)'
    assert view.catalogue(limit=2).endswith('… and 1 more files; search finds them.')
    # Parts without a PDF page number have dN.oK handles; the line names them.
    slides = ToolView({'s': {'filename': 'cours.odp', 'pages': [
        {'page_id': 'x1', 'pdf_page_no': None}, {'page_id': 'x2', 'pdf_page_no': None}]}})
    assert slides.catalogue() == 'd1 cours.odp (2 parts, d1.o1–d1.o2)'


def test_sampling_is_qwen_advice_unless_given():
    from backend.app.agent.model import GREEDY, QWEN_NON_THINKING, ResearchModel
    from backend.app.services.model_routing import Endpoint
    endpoint = Endpoint('http://x/v1', 'k', 'm')
    from backend.app.services.research_vision import ResearchVision
    qwen = ResearchModel(endpoint)
    assert qwen.sampling == {'temperature': 0.7, 'top_p': 0.8} and qwen.extra_body == {'top_k': 20}
    assert QWEN_NON_THINKING['top_k'] == 20  # the shared setting is not changed
    greedy = ResearchModel(endpoint, sampling=GREEDY)
    assert greedy.sampling == {'temperature': 0} and greedy.extra_body == {}
    # Vision calls follow the same setting.
    assert (ResearchVision(endpoint).sampling, ResearchVision(endpoint).extra_body) == (
        qwen.sampling, qwen.extra_body)
    assert ResearchVision(endpoint, sampling=GREEDY).sampling == {'temperature': 0}


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
    # First a turn with the tools to read; then one correction without them.
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [E7].', 'Toujours [E7].', 'Réponse [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert 'E1' in model.seen[-1][-1]['content'] and model.with_tools[-1] is False
    assert result.answer == 'Réponse [E1].' and result.stop_reason == 'finished'


@pytest.mark.asyncio
async def test_an_answer_written_from_memory_is_sent_back_to_read():
    # Seen with Qwen3.8-27B: a summary of page 23 with invented IDs, no tool call.
    model = Script('Résumé de la page 10 [E5][E6].', [('read', {'page': 'd1.p10'})],
                   'Vin3 = product(Vin1, Années) [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Résume la page 10')
    read_turn = model.seen[1][-1]['content']
    assert read_turn.startswith('Your answer cites E5, E6, which no tool returned') and model.with_tools[1]
    assert result.answer == 'Vin3 = product(Vin1, Années) [E1].' and result.stop_reason == 'finished'
    assert [c['evidence_id'] for c in result.citations] == ['E1']


@pytest.mark.asyncio
async def test_unknown_evidence_id_twice_gives_the_pages_read():
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [E7].', 'Encore [E7].', 'Toujours [E8].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.stop_reason == 'partial_invalid_evidence_ids' and '[E1]' in result.answer
    # The listed pages are real citations, so the student can open them.
    assert [c['evidence_id'] for c in result.citations] == ['E1']
    assert result.unresolved == ['research_incomplete']


def test_malformed_references_are_found_outside_code():
    from backend.app.agent.result import malformed_refs
    text = 'Voir [E8.p14.b1] et [d6.p24.b14], puis [E1, E2] et [E3]. `t[d1]` ```\nx[E2.b]\n```'
    assert malformed_refs(text) == ['[E8.p14.b1]', '[d6.p24.b14]']
    assert malformed_refs('Rien [E1]; une liste [Exemple] ou [dessin].') == []
    # A file and page instead of an ID, as Flash-Next once wrote (A7).
    assert malformed_refs('Shannon [EASI951_CM_2025.pdf, page 42] et [SQL, p. 3].') == [
        '[EASI951_CM_2025.pdf, page 42]', '[SQL, p. 3]']
    assert malformed_refs('Un tableau [1, 2, 3] et une liste [a, b].') == []
    # Handles outside brackets, as an answer once wrote them.
    assert malformed_refs('Vues (d6.p19), puis d6.p43–d6.p48 et d1.p20.b3.') == ['d6.p19', 'd6.p43', 'd6.p48', 'd1.p20.b3']
    assert malformed_refs('Le fichier cd1.p2x et `d1.p20` en code, version 2.d1.p3 ne comptent pas.') == []


@pytest.mark.asyncio
async def test_a_malformed_reference_gets_the_correction_turn():
    # A handle cannot be rewritten as an ID: the model corrects it.
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [d1.p10.b2].', 'Réponse [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    correction = model.seen[-1][-1]['content']
    assert '[d1.p10.b2]' in correction and 'E1' in correction and model.with_tools[-1] is False
    assert result.answer == 'Réponse [E1].' and [c['evidence_id'] for c in result.citations] == ['E1']


@pytest.mark.asyncio
async def test_a_malformed_reference_left_after_correction_keeps_the_answer():
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [d1.p10] [E1].', 'Réponse [d1.p10] [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.stop_reason == 'finished' and result.answer == 'Réponse [d1.p10] [E1].'


@pytest.mark.asyncio
async def test_an_unusable_correction_keeps_an_answer_whose_ids_were_valid():
    # Seen with Flash-Next (W17): the correction turn has no tools, the model
    # wrote a tool call, and a good answer became the failure text.
    model = Script([('read', {'page': 'd1.p10'})], 'Vin3 [E1], voir [d1.p10].', '<tool_call>read</tool_call>')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.answer == 'Vin3 [E1], voir [d1.p10].' and result.stop_reason == 'finished'
    assert [c['evidence_id'] for c in result.citations] == ['E1']


@pytest.mark.asyncio
async def test_a_correction_with_new_unknown_ids_keeps_an_answer_whose_ids_were_valid():
    model = Script([('read', {'page': 'd1.p10'})], 'Vin3 [E1], voir [d1.p10].', 'Vin3 [E1][E4].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.answer == 'Vin3 [E1], voir [d1.p10].' and result.stop_reason == 'finished'


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
async def test_a_greeting_is_checked_once_and_kept():
    model = Script('Bonjour ! Que cherches-tu ?', 'DONE')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Bonjour')
    assert result.answer == 'Bonjour ! Que cherches-tu ?' and result.stop_reason == 'finished'
    assert model.purposes == ['agent', 'answer_check'] and model.with_tools == [True, True]
    assert model.seen[1][-1]['content'].startswith('The student has not seen this reply yet.')


@pytest.mark.asyncio
@pytest.mark.parametrize('check_reply', ['DONE — a greeting, no course material.', 'done.', '',
                                         '<tool_call>{"name": "search"}</tool_call>'])
async def test_done_with_words_or_an_unusable_check_reply_keeps_the_reply(check_reply):
    model = Script('Bonjour ! Que cherches-tu ?', check_reply)
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Bonjour')
    assert result.answer == 'Bonjour ! Que cherches-tu ?' and result.stop_reason == 'finished'


@pytest.mark.asyncio
async def test_a_promise_without_a_tool_call_leads_to_research():
    # Seen with Qwen3.8-27B: "Laissez-moi la lire..." and no tool call.
    model = Script('Je dois vérifier la page 10. Laissez-moi la lire.',
                   [('read', {'page': 'd1.p10'})], 'Vin3 = product(Vin1, Années) [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Que dit la page 10 ?')
    assert result.answer == 'Vin3 = product(Vin1, Années) [E1].'
    assert [c['evidence_id'] for c in result.citations] == ['E1'] and result.tool_calls == 1
    assert model.purposes == ['agent', 'answer_check', 'agent']


@pytest.mark.asyncio
async def test_a_new_reply_to_the_check_is_the_answer_and_is_not_checked_again():
    model = Script('[assistant stopped]', 'Je ne peux pas répondre sans le cours.')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.answer == 'Je ne peux pas répondre sans le cours.' and len(model.purposes) == 2


@pytest.mark.asyncio
async def test_a_failed_check_keeps_the_reply():
    class Failing(Script):
        async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
            if purpose == 'answer_check':
                raise ConnectionError('down')
            return await super().__call__(messages, max_output, timeout, tools=tools, purpose=purpose)
    result = await ResearchRunner(CourseTools(ViewFixture()), Failing('Merci, à bientôt !')).run('Merci')
    assert result.answer == 'Merci, à bientôt !' and result.stop_reason == 'finished'


@pytest.mark.asyncio
async def test_history_is_sent_as_turns_with_their_evidence_ids():
    model = Script('Oui.', 'DONE')
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


def test_each_tool_call_and_runner_event_is_logged(caplog):
    import asyncio
    import logging
    model = Script([('read', {'page': 'd1.p10'})], 'Réponse [E7].', 'Réponse [E1].', 'Réponse [E1].')
    with caplog.at_level(logging.INFO, logger='backend.app.agent.runner'):
        asyncio.run(ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?'))
    lines = [r.getMessage() for r in caplog.records if 'research run=' in r.getMessage()]
    assert 'tool=read' in lines[0] and 'ok=True' in lines[0]
    # The turns between the tool calls can be followed too.
    events = [line.split('event=')[1].split()[0] for line in lines[1:]]
    assert events == ['turn', 'unknown_ids_read_turn', 'finished']
    assert '["E7"]' in lines[2] and '"stop": "finished"' in lines[3]


@pytest.mark.asyncio
async def test_a_later_run_continues_the_numbering_and_corrects_old_ids():
    # The session's earlier answer cited E1-E2; this run starts at E3.
    model = Script([('read', {'page': 'd1.p10'})], 'Encore [E1].', 'Encore [E3].')
    result = await ResearchRunner(CourseTools(ViewFixture(), first_evidence=3), model).run(
        'Et la page 10 ?', history=[{'role': 'user', 'content': 'Q ?'},
                                    {'role': 'assistant', 'content': 'Voir [E1, E2].'}])
    # E1 is not this run's, and no earlier citation was passed: back to the tools.
    assert 'cites E1, which no tool returned' in model.seen[-1][-1]['content'] and model.with_tools[-1]
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
async def test_a_research_call_ends_with_the_research_time_and_the_answer_follows():
    # research 300 s, total 420 s, one call 120 s: a research call that starts
    # at 295 s gets 5 s, so the answer keeps its time.
    now, timeouts = [0.0], []
    model = Script([('read', {'page': 'd1.p10'})], 'Vin3 [E1].')

    async def timed(messages, max_output, timeout, *, tools=None, purpose=''):
        timeouts.append(timeout)
        if len(timeouts) == 2:
            now[0] = 300.0
            raise TimeoutError
        reply = await model(messages, max_output, timeout, tools=tools, purpose=purpose)
        now[0] = 295.0
        return reply
    result = await ResearchRunner(CourseTools(ViewFixture()), timed, clock=lambda: now[0],
                                  research_timeout=300, total_timeout=420, model_timeout=120).run('Q ?')
    assert timeouts == [120, 5, 120]
    assert result.answer == 'Vin3 [E1].' and [c['evidence_id'] for c in result.citations] == ['E1']
    assert {'event': 'tools_withdrawn', 'reason': 'research_time'} in result.trace


@pytest.mark.asyncio
async def test_cancellation_during_a_model_call_stops_the_run():
    async def cancelled(*args, **kwargs):
        raise ResearchRunStopped('run_not_running')
    with pytest.raises(ResearchRunStopped):
        await ResearchRunner(CourseTools(ViewFixture()), cancelled).run('Q ?')


@pytest.mark.asyncio
async def test_calls_that_may_answer_stream_a_draft_and_a_tool_turn_withdraws_it():
    drafts = []

    class Streaming(Script):
        async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
            reply = await super().__call__(messages, max_output, timeout, tools=tools, purpose=purpose)
            self.streamed = getattr(self, 'streamed', []) + [on_text is not None]
            if on_text:
                on_text('Je regarde. ' if reply.tool_calls else reply.content)
            return reply
    model = Streaming([('read', {'page': 'd1.p10'})], 'Vin3 [E1], voir [d1.p10].', 'Vin3 [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model, on_draft=drafts.append).run('Q ?')
    assert result.answer == 'Vin3 [E1].'
    # agent calls stream; the citation correction does not.
    assert model.purposes == ['agent', 'agent', 'citation_correction'] and model.streamed == [True, True, False]
    assert drafts == ['Je regarde. ', None, 'Vin3 [E1], voir [d1.p10].']


@pytest.mark.asyncio
async def test_without_a_draft_callback_no_call_streams():
    model = Script([('read', {'page': 'd1.p10'})], 'Vin3 [E1].')  # Script takes no on_text
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.answer == 'Vin3 [E1].'


class Offered(Script):
    """A Script that also records the tool names offered to each call."""
    async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
        self.offered = getattr(self, 'offered', []) + [[t['function']['name'] for t in tools or []]]
        return await super().__call__(messages, max_output, timeout, tools=tools, purpose=purpose)


ASK = ('ask_student', {'question': "Quel exercice 3 ?", 'options': ['Graphes, p. 12', 'SQL, p. 30']})


@pytest.mark.asyncio
async def test_a_question_back_ends_the_run_without_check_or_correction():
    model = Offered([ASK])
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run("j'ai pas compris l'exo 3", allow_ask=True)
    assert (result.answer, result.stop_reason, result.citations) == ("Quel exercice 3 ?", 'clarification', [])
    assert result.clarification == {'question': "Quel exercice 3 ?", 'options': ['Graphes, p. 12', 'SQL, p. 30']}
    # One call: no answer check, no tool slot used.
    assert model.purposes == ['agent'] and result.tool_calls == 0
    assert 'ask_student' in model.offered[0]
    assert {'event': 'ask_student', **result.clarification} in result.trace


@pytest.mark.asyncio
async def test_ask_student_is_offered_only_when_allowed():
    model = Offered('Bonjour !', 'DONE')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Bonjour')
    assert 'ask_student' not in model.offered[0] and result.clarification is None
    # Not offered: a call to it is an unknown tool, as any other name.
    model = Offered([ASK], 'Réponse générale.')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.stop_reason == 'finished' and result.clarification is None
    assert 'unknown_tool (ask_student)' in model.seen[1][-1]['content']


@pytest.mark.asyncio
async def test_research_may_come_first_and_ask_must_be_alone():
    model = Offered([('read', {'page': 'd1.p10'}), ASK], [ASK])
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?', allow_ask=True)
    tool_texts = [m['content'] for m in model.seen[1] if m['role'] == 'tool']
    assert tool_texts[1] == 'error: call ask_student alone, after the other tools have returned'
    assert result.stop_reason == 'clarification' and result.tool_calls == 1


@pytest.mark.asyncio
async def test_a_question_back_with_bad_options_is_returned_to_the_model():
    bad = ('ask_student', {'question': 'Lequel ?', 'options': ['SQL']})
    model = Offered([bad], 'Réponse générale.')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?', allow_ask=True)
    assert model.seen[1][-1]['content'].startswith('error: invalid_arguments (')
    assert result.stop_reason == 'finished' and result.answer == 'Réponse générale.'


def test_a_question_back_names_no_handles():
    from backend.app.agent.runner import AskStudentArgs
    ask = AskStudentArgs.model_validate({'question': 'Quelle formule (d1) ?', 'options': [
        "la formule d'algèbre relationnelle (d1/d7)", 'une formule sur les graphes (d2/d10/d11)', 'SQL d6']})
    assert ask.question == 'Quelle formule ?'
    assert ask.options == ["la formule d'algèbre relationnelle", 'une formule sur les graphes', 'SQL']
    # Options that are only handles are empty, hence refused.
    with pytest.raises(ValueError):
        AskStudentArgs.model_validate({'question': 'Lequel ?', 'options': ['d1', 'd2']})


@pytest.mark.asyncio
async def test_a_valid_id_with_extra_parts_is_repaired_without_a_correction_turn():
    # Seen with Flash-Next (B7): "[E21 in d1.p21]".
    model = Script([('read', {'page': 'd1.p10'})], 'Vin3 [E1 in d1.p10] et [E1.p10.b2].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.answer == 'Vin3 [E1] et [E1].' and model.purposes == ['agent', 'agent']
    assert [c['evidence_id'] for c in result.citations] == ['E1']


@pytest.mark.asyncio
async def test_a_correction_that_drops_every_citation_keeps_the_answer():
    # Seen with Flash-Next (B7): the correction turn replied with a note.
    model = Script([('read', {'page': 'd1.p10'})], 'Vin3 [E1], voir d1.p10.',
                   'Voici la correction (la faute était une référence mal formée).')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.answer == 'Vin3 [E1], voir d1.p10.' and result.stop_reason == 'finished'


@pytest.mark.asyncio
async def test_a_file_and_page_instead_of_an_id_gets_the_correction_turn():
    model = Script([('read', {'page': 'd1.p10'})], 'Vin3 [Algebre.pdf, page 10].', 'Vin3 [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert model.purposes[-1] == 'citation_correction' and '[Algebre.pdf, page 10]' in model.seen[-1][-1]['content']
    assert result.answer == 'Vin3 [E1].' and [c['evidence_id'] for c in result.citations] == ['E1']


def test_handles_next_to_file_names_are_removed_outside_code():
    from backend.app.agent.runner import _without_document_handles as clean
    assert clean('d1 – Algebre_relationnelle_2020.pdf (27 pages) : le cours') == 'Algebre_relationnelle_2020.pdf (27 pages) : le cours'
    assert clean('Voir **d2: `SQL.pdf`** et la vue (d6.p19), ou (d1/d7).') == 'Voir **`SQL.pdf`** et la vue, ou.'
    assert clean('La distance d1 - d2 vaut 3 (d1 est le départ).') == 'La distance d1 - d2 vaut 3 (d1 est le départ).'
    assert clean('```\nd1 - a.pdf (d2)\n```') == '```\nd1 - a.pdf (d2)\n```'


@pytest.mark.asyncio
async def test_an_answer_with_handles_in_parentheses_needs_no_correction():
    model = Script([('read', {'page': 'd1.p10'})], 'Vin3 [E1] (d1.p10), cours d1 – Algebre.pdf.')
    result = await ResearchRunner(CourseTools(ViewFixture()), model).run('Q ?')
    assert result.answer == 'Vin3 [E1], cours Algebre.pdf.' and model.purposes == ['agent', 'agent']
