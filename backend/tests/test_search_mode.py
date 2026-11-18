"""Search mode: one search, the found and adjacent pages, one answer."""
import json
from uuid import uuid4

import pytest

from backend.app.agent.course_tools import CourseTools
from backend.app.agent.language import GENERIC_SENTENCE
from backend.app.agent.model import ModelReply
from backend.app.agent.search import CHECK_TURN, NO_PAGES, SEARCH_PROMPT, SearchRunner, _queries
from backend.app.services.retrieval_service import ChunkHit, apply_rerank
from backend.tests.test_course_tools import ViewFixture


class Script:
    """Answers in order; records the messages and purpose of each call."""

    def __init__(self, *answers, fail=False):
        self.answers, self.seen, self.purposes, self.fail = list(answers), [], [], fail

    async def __call__(self, messages, max_output, timeout, *, tools=None, purpose='', on_text=None):
        assert tools is None  # search mode never offers tools
        self.seen.append(json.loads(json.dumps(messages)))
        self.purposes.append(purpose)
        if self.fail:
            raise RuntimeError('model down')
        text = self.answers.pop(0)
        if on_text:
            on_text(text)
        return ModelReply(text, [], 'flash-next', 100, 10)


class Scored(ViewFixture):
    """The fixture's search hit with a reranker score."""

    def __init__(self, score):
        super().__init__()
        self.score = score

    async def execute(self, name, arguments):
        result = await super().execute(name, arguments)
        if name == 'search':
            result['items'][0]['score'] = self.score
        return result


class Empty(ViewFixture):
    async def page_index(self):
        return {}


def searches(tools):
    return [args['query'] for name, args in tools.calls if name == 'search']


def reads(tools):
    return [args['page_id'] for name, args in tools.calls if name == 'read']


@pytest.mark.asyncio
async def test_the_question_is_searched_and_answered_from_found_and_adjacent_pages():
    tools = ViewFixture()
    model = Script('Le produit cartésien combine les tuples [E3], voir [E5] et [E99].')
    result = await SearchRunner(CourseTools(tools), model).run(
        'Produit cartésien ?', teaching_rules='RULES', research_note='NOTE')
    assert searches(tools) == ['Produit cartésien ?']
    # The found page first, then its neighbour.
    assert reads(tools) == [str(tools.p9), str(tools.p10)]
    assert model.purposes == ['search_answer']
    system, user = model.seen[0][0]['content'], model.seen[0][-1]['content']
    assert system.startswith(SEARCH_PROMPT) and 'RULES' in system and system.endswith('NOTE')
    assert user.startswith('Produit cartésien ?\n\nCourse pages found:')
    assert '--- Algebre.pdf, page 9 ---\n[E1] Exemple\n[E3] Produit cartésien' in user
    assert '--- Algebre.pdf, page 10 ---\n[E5] Vin3' in user
    # The footer number is not shown; the cut page says so.
    assert '[E2]' not in user and '(page text shortened)' in user
    # An ID no page gave is removed, the others become citations.
    assert result.answer == 'Le produit cartésien combine les tuples [E3], voir [E5] et .'
    assert [c['evidence_id'] for c in result.citations] == ['E3', 'E5']
    assert result.stop_reason == 'finished' and result.model_calls == 1


@pytest.mark.asyncio
async def test_history_stays_as_turns_and_earlier_ids_stay_valid():
    tools = ViewFixture()
    earlier = {'E40': {'evidence_id': 'E40', 'document_id': str(tools.doc)}}
    model = Script('Comme avant [E40], et [E3].')
    result = await SearchRunner(CourseTools(tools, first_evidence=1), model).run(
        'Et ensuite ?', history=[{'role': 'user', 'content': 'Q1'},
                                 {'role': 'assistant', 'content': 'R1 [E40]'}],
        earlier_citations=earlier)
    assert [m['role'] for m in model.seen[0]] == ['system', 'user', 'assistant', 'user']
    assert [c['evidence_id'] for c in result.citations] == ['E3', 'E40']


@pytest.mark.asyncio
async def test_rewrite_searches_each_query():
    tools = ViewFixture()
    model = Script('{"queries": ["produit cartésien", "jointure naturelle", "produit cartésien"]}',
                   'Réponse [E3].')
    result = await SearchRunner(CourseTools(tools), model, rewrite=True).run(
        'Et la jointure ?', history=[{'role': 'user', 'content': 'Le produit cartésien ?'},
                                     {'role': 'assistant', 'content': 'Il combine [E1].'}])
    assert model.purposes == ['search_query', 'search_answer']
    assert searches(tools) == ['produit cartésien', 'jointure naturelle']
    # The rewrite sees the file list and the conversation.
    assert 'Algebre.pdf' in model.seen[0][0]['content']
    assert 'Le produit cartésien ?' in model.seen[0][1]['content']
    assert result.trace[-1]['event'] == 'finished'
    assert {'event': 'search_queries', 'queries': ['produit cartésien', 'jointure naturelle'],
            'source': 'rewrite'} in result.trace


def test_an_unusable_rewrite_falls_back_to_the_question():
    assert _queries('not json', 'Q ?') == (['Q ?'], 'rewrite_failed')
    assert _queries('{"queries": []}', 'Q ?') == (['Q ?'], 'rewrite_failed')
    assert _queries('Voici: {"queries": ["a", " b ", "c", "d"]}', 'Q') == (['a', 'b', 'c'], 'rewrite')


@pytest.mark.asyncio
async def test_an_answer_without_citations_gets_one_check():
    tools = ViewFixture()
    model = Script('Le produit cartésien combine les tuples.', 'Le produit cartésien combine les tuples [E3].')
    result = await SearchRunner(CourseTools(tools), model).run('Produit cartésien ?')
    assert model.purposes == ['search_answer', 'search_check']
    assert model.seen[1][-1]['content'] == CHECK_TURN + '\n\n' + GENERIC_SENTENCE
    assert result.answer.endswith('[E3].') and len(result.citations) == 1


@pytest.mark.asyncio
async def test_done_keeps_a_reply_that_needs_no_course_page():
    model = Script('Je vous en prie !', 'DONE')
    result = await SearchRunner(CourseTools(ViewFixture()), model).run('Merci')
    assert result.answer == 'Je vous en prie !' and result.citations == []
    assert result.stop_reason == 'finished'


@pytest.mark.asyncio
async def test_a_weak_best_score_means_not_found_without_reading():
    tools = Scored(0.01)
    model = Script("Le cours ne traite pas de Dijkstra ; essayez le mode Research.")
    result = await SearchRunner(CourseTools(tools), model, min_score=0.1).run('Dijkstra ?')
    assert reads(tools) == [] and result.stop_reason == 'not_found'
    assert model.seen[0][-1]['content'] == 'Dijkstra ?\n\n' + NO_PAGES + '\n\n' + GENERIC_SENTENCE
    # No check turn: no page was given.
    assert model.purposes == ['search_answer']
    found = next(e for e in result.trace if e['event'] == 'search_found')
    assert found['best_score'] == 0.01 and found['below_min_score']


@pytest.mark.asyncio
async def test_a_good_score_reads_the_pages():
    tools = Scored(0.8)
    result = await SearchRunner(CourseTools(tools), Script('Oui [E3].'), min_score=0.1).run('Produit ?')
    assert reads(tools) and result.stop_reason == 'finished'


@pytest.mark.asyncio
async def test_a_lab_without_documents_says_so():
    tools = Empty()
    model = Script("Aucun document n'a encore été publié pour ce TP.")
    result = await SearchRunner(CourseTools(tools), model).run('Produit ?')
    assert searches(tools) == [] and result.stop_reason == 'no_documents'
    assert 'no course documents yet' in model.seen[0][-1]['content']


@pytest.mark.asyncio
async def test_a_failed_model_call_still_answers_the_student():
    result = await SearchRunner(CourseTools(ViewFixture()), Script(fail=True)).run('Produit ?')
    assert result.stop_reason == 'partial_model_unavailable'
    assert result.answer.startswith("La recherche n'a pas pu être terminée")


@pytest.mark.asyncio
async def test_the_answer_is_streamed_as_a_draft():
    drafts = []
    await SearchRunner(CourseTools(ViewFixture()), Script('Oui [E3].'),
                       on_draft=drafts.append).run('Produit ?')
    assert drafts == ['Oui [E3].']


@pytest.mark.asyncio
async def test_the_reranker_score_comes_with_each_hit():
    hits = [ChunkHit(content=c, page_no=1, document_id=None, id=n) for n, c in enumerate('abc')]

    async def rerank(query, documents):
        return [0.1, 0.9, 0.5]

    ranked, filtered = await apply_rerank('q', hits, rerank, top_k=2)
    assert [(h.content, h.score) for h in ranked] == [('b', 0.9), ('c', 0.5)] and not filtered


@pytest.mark.asyncio
async def test_concise_asks_for_a_short_answer():
    from backend.app.agent.search import CONCISE_NOTE
    model = Script('Oui [E3].', 'Oui [E3].')
    await SearchRunner(CourseTools(ViewFixture()), model, concise=True).run('Produit ?')
    await SearchRunner(CourseTools(ViewFixture()), model).run('Produit ?')
    assert CONCISE_NOTE in model.seen[0][0]['content'] and CONCISE_NOTE not in model.seen[1][0]['content']


def test_product_settings_follow_the_comparison():
    from backend.app.agent import search
    assert (search.REWRITE, search.MIN_SCORE, search.CONCISE) == (False, 0.001, True)
    # The "no page" bound follows the reranker's scale.
    assert search.min_score_for('BAAI/bge-reranker-v2-m3') == 0.001
    assert search.min_score_for('Qwen/Qwen3-Reranker-0.6B') == 0.5
    assert search.min_score_for(None) == 0.001


class TwoFiles:
    """A class course (main.pdf, 2 pages) and the TP statement (TP1.pdf, 2 pages) of one lab.
    The main search finds the course's page 1 (and the TP's page 2 with tp_hit); a
    search inside the TP file finds its page 1."""

    def __init__(self, course_score=0.9, course_hit=True, tp_hit=False, unreadable=()):
        from backend.app.agent.tools import ResearchScope
        self.course, self.tp = uuid4(), uuid4()
        self.c1, self.c2, self.t1, self.t2 = (str(uuid4()) for _ in range(4))
        self.scope = ResearchScope(*(uuid4() for _ in range(5)), {self.course: uuid4(), self.tp: uuid4()})
        self.course_score, self.course_hit, self.tp_hit, self.unreadable = course_score, course_hit, tp_hit, unreadable
        self.calls = []
        self.text = {self.c1: 'Le langage C gère la mémoire', self.c2: 'Pointeurs',
                     self.t1: 'TP1 : objectifs, rendu en binôme', self.t2: 'Question 3 : tracer la courbe'}
        self.number = {self.c1: 1, self.c2: 2, self.t1: 1, self.t2: 2}

    async def page_index(self):
        return {str(self.course): {'filename': 'main.pdf', 'lab_name': None, 'pages': [
                    {'page_id': self.c1, 'pdf_page_no': 1}, {'page_id': self.c2, 'pdf_page_no': 2}]},
                str(self.tp): {'filename': 'TP1.pdf', 'lab_name': 'TP1', 'pages': [
                    {'page_id': self.t1, 'pdf_page_no': 1}, {'page_id': self.t2, 'pdf_page_no': 2}]}}

    async def _revision(self, document_id):
        pass

    async def _check_session(self):
        pass

    def _hit(self, document_id, page_id, score):
        return {'document_id': str(document_id), 'revision_id': str(document_id), 'page_id': page_id,
                'pdf_page_no': self.number[page_id], 'block_id': page_id, 'type': 'text', 'score': score,
                'source_completeness': 'complete', 'issues': [],
                'snippets': [{'chunk_id': page_id, 'range': {'start': 0, 'end': 4}, 'content': 'x'}]}

    async def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if name == 'search' and arguments.get('document_id') == str(self.tp):
            return {'ok': True, 'issues': [], 'items': [self._hit(self.tp, self.t1, 0.2)]}
        if name == 'search':
            return {'ok': True, 'issues': [], 'items': (
                [self._hit(self.tp, self.t2, 0.95)] if self.tp_hit else []) + (
                [self._hit(self.course, self.c1, self.course_score)] if self.course_hit else [])}
        if name == 'read' and arguments['page_id'] not in self.unreadable:
            page_id = arguments['page_id']
            text = self.text[page_id]
            return {'ok': True, 'issues': [], 'truncated': False, 'next_cursor': None, 'items': [{
                'block_id': page_id, 'type': 'text', 'content': text,
                'page': {'page_id': page_id, 'order_index': 0, 'source_slide_no': 1,
                         'preview_page_no': self.number[page_id], 'preview_asset_id': None},
                'range': {'start': 0, 'end': len(text)}, 'block_length': len(text),
                'returned_complete_block': True, 'source': {'content_origin': 'pdf_text_layer'},
                'source_completeness': 'complete', 'issues': [], 'index_state': 'searchable',
                'edited_by_teacher': False, 'assets': []}]}
        return {'ok': False, 'error': {'code': 'not_found', 'message': name}}


@pytest.mark.asyncio
async def test_the_tp_text_follows_the_rules_and_a_tp_citation_is_kept():
    from backend.app.agent.search import SOURCES_NOTE, TP_HEADING
    tools = TwoFiles()
    model = Script('Le TP se fait en binôme [E1], en C [E3].')
    result = await SearchRunner(CourseTools(tools), model).run('What are we doing?', teaching_rules='RULES')
    system, user = model.seen[0][0]['content'], model.seen[0][-1]['content']
    # The system prompt, the rules, the note, then every page of the TP with its IDs.
    assert system.startswith(SEARCH_PROMPT)
    assert system.index('RULES') < system.index(SOURCES_NOTE) < system.index(TP_HEADING)
    assert system.endswith(TP_HEADING + '\n\n--- [This TP] TP1.pdf, page 1 ---\n[E1] TP1 : objectifs, rendu en '
                           'binôme\n\n--- [This TP] TP1.pdf, page 2 ---\n[E2] Question 3 : tracer la courbe')
    # The question comes with the class course pages only, after the TP's IDs.
    assert user.startswith('What are we doing?\n\nCourse pages found:\n\n--- [Class course] main.pdf, page 1 ---\n'
                           '[E3] Le langage C')
    assert 'TP1.pdf' not in user
    # A TP line is cited like a found page.
    assert result.answer == 'Le TP se fait en binôme [E1], en C [E3].'
    assert [(c['evidence_id'], c['page_id']) for c in result.citations] == [('E1', tools.t1), ('E3', tools.c1)]
    assert {'event': 'tp_text', 'pages': 2, 'chars': len(system) - system.index('--- [This TP]'),
            'in_prompt': True, 'left_out': 0} in result.trace


@pytest.mark.asyncio
async def test_tp_pages_found_by_the_search_are_not_given_twice_nor_use_the_page_budget():
    tools = TwoFiles(tp_hit=True)
    model = Script('Voir [E2].')
    budget = len('[E3] Le langage C gère la mémoire')
    await SearchRunner(CourseTools(tools), model, context_chars=budget).run('Question 3 ?')
    user = model.seen[0][-1]['content']
    assert 'TP1.pdf' not in user and '--- [Class course] main.pdf, page 1 ---\n[E3] Le langage C' in user
    # Each TP page is read once, for the TP text.
    assert sorted(reads(tools)).count(tools.t2) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('files', [TwoFiles(course_score=0.01), TwoFiles(course_hit=False)])
async def test_a_weak_or_empty_course_search_still_answers_from_the_tp_text(files):
    from backend.app.agent.search import NO_CLASS_PAGES
    # Uncited first: the TP pages count as read, so the check turn asks for the IDs.
    model = Script('Le rendu se fait en binôme.', 'Le rendu se fait en binôme [E1].')
    result = await SearchRunner(CourseTools(files), model, min_score=0.5).run('On fait quoi ?')
    assert model.seen[0][-1]['content'] == 'On fait quoi ?\n\n' + NO_CLASS_PAGES + '\n\n' + GENERIC_SENTENCE
    assert '[E1] TP1 : objectifs' in model.seen[0][0]['content']
    assert model.purposes == ['search_answer', 'search_check']
    assert result.stop_reason == 'finished' and [c['evidence_id'] for c in result.citations] == ['E1']


@pytest.mark.asyncio
async def test_an_unreadable_tp_page_says_so():
    from backend.app.agent.tp_text import UNREADABLE
    tools = TwoFiles(unreadable=())
    tools.unreadable = (tools.t2,)
    model = Script('Voir [E1].')
    await SearchRunner(CourseTools(tools), model).run('On fait quoi ?')
    assert '--- [This TP] TP1.pdf, page 2 ---\n' + UNREADABLE in model.seen[0][0]['content']


@pytest.mark.asyncio
async def test_a_tp_text_over_the_limit_gives_its_best_pages_with_the_found_ones(monkeypatch):
    from backend.app.agent import tp_text
    from backend.app.agent.search import SOURCES_NOTE, TP_HEADING
    monkeypatch.setattr(tp_text, 'TP_CHARS', 50)
    tools = TwoFiles(course_score=0.01)
    model = Script('Voir [E1].')
    result = await SearchRunner(CourseTools(tools), model, min_score=0.5).run('On fait quoi ?')
    system, user = model.seen[0][0]['content'], model.seen[0][-1]['content']
    assert TP_HEADING not in system and system.endswith(SOURCES_NOTE)
    # The best page of a search inside the TP file, then its neighbour; the weak course page is left out.
    assert ('search', {'query': 'On fait quoi ?', 'document_id': str(tools.tp), 'top_k': 3}) in tools.calls
    assert user.index('--- [This TP] TP1.pdf, page 1 ---') < user.index('--- [This TP] TP1.pdf, page 2 ---')
    assert 'main.pdf' not in user and result.stop_reason == 'finished'
    # The IDs follow the pages the model was given.
    assert user.startswith('On fait quoi ?\n\nCourse pages found:\n\n--- [This TP] TP1.pdf, page 1 ---\n[E1] TP1')
    event = next(e for e in result.trace if e['event'] == 'tp_text')
    assert event['chars'] == 0 and not event['in_prompt'] and event['left_out'] == 1


@pytest.mark.asyncio
async def test_without_a_tp_file_the_prompt_is_as_before():
    class ClassFileOnly(ViewFixture):
        async def page_index(self):
            index = await super().page_index()
            return {d: {**entry, 'lab_name': None} for d, entry in index.items()}
    for tools in (ViewFixture(), ClassFileOnly()):
        model = Script('Oui [E3].')
        result = await SearchRunner(CourseTools(tools), model).run(
            'Produit ?', teaching_rules='RULES', research_note='NOTE')
        assert model.seen[0][0]['content'] == SEARCH_PROMPT + '\nRULES\nNOTE'
        assert model.seen[0][-1]['content'].startswith(
            'Produit ?\n\nCourse pages found:\n\n--- Algebre.pdf, page 9 ---\n[E1] Exemple')
        assert reads(tools) == [str(tools.p9), str(tools.p10)]
        assert not [e for e in result.trace if e['event'] == 'tp_text']


@pytest.mark.asyncio
async def test_the_tp_keeps_its_ids_in_the_next_question():
    first = Script('En binôme [E1], en C [E3].')
    tools = TwoFiles()
    earlier = await SearchRunner(CourseTools(tools), first).run('On fait quoi ?')
    # The session's next ID is E4: the next run's TP text is the same, its course pages come after.
    second = Script('Voir [E2].')
    await SearchRunner(CourseTools(tools, first_evidence=4), second).run(
        'Et la question 3 ?', history=[{'role': 'user', 'content': 'On fait quoi ?'},
                                       {'role': 'assistant', 'content': earlier.answer}],
        earlier_citations={c['evidence_id']: c for c in earlier.citations})
    assert second.seen[0][0]['content'] == first.seen[0][0]['content']
    assert '--- [Class course] main.pdf, page 1 ---\n[E4] Le langage C' in second.seen[0][-1]['content']


@pytest.mark.asyncio
async def test_the_tp_takes_new_ids_when_an_earlier_answer_used_its_numbers():
    tools = TwoFiles()
    other = {'evidence_id': 'E1', 'document_id': str(tools.course), 'revision_id': str(uuid4()),
             'page_id': tools.c2, 'block_id': tools.c2, 'range': {'start': 0, 'end': 9}}
    model = Script('Voir [E7].')
    result = await SearchRunner(CourseTools(tools, first_evidence=7), model).run(
        'On fait quoi ?', earlier_citations={'E1': other})
    assert '--- [This TP] TP1.pdf, page 1 ---\n[E7] TP1 : objectifs' in model.seen[0][0]['content']
    assert '[E9] Le langage C' in model.seen[0][-1]['content']
    assert [(c['evidence_id'], c['page_id']) for c in result.citations] == [('E7', tools.t1)]


class TPAndSlides(TwoFiles):
    """Both files in the TP: the statement (2 short pages) and course slides
    (2 long pages) that the teacher put in the TP too."""

    def __init__(self):
        super().__init__()
        self.text[self.c1] = 'Diapositive : la mémoire en C. ' * 20
        self.text[self.c2] = 'Diapositive : les pointeurs. ' * 20

    async def page_index(self):
        index = await super().page_index()
        index[str(self.course)] = {**index[str(self.course)], 'filename': 'Slides.pdf', 'lab_name': 'TP1'}
        return index


@pytest.mark.asyncio
async def test_tp_files_go_in_whole_the_shortest_first_and_the_rest_is_searched():
    from backend.app.agent.search import TP_HEADING
    tools = TPAndSlides()
    model = Script('Le TP se fait en binôme [E1].')
    # Room for the statement, not for the slides.
    result = await SearchRunner(CourseTools(tools), model, tp_chars=300).run('On fait quoi ?')
    system, user = model.seen[0][0]['content'], model.seen[0][-1]['content']
    tp_part = system[system.index(TP_HEADING):]
    assert '--- [This TP] TP1.pdf, page 1 ---\n[E1] TP1 : objectifs' in tp_part and 'Slides.pdf' not in tp_part
    # The slides are searched as before: the found page comes with the question.
    assert 'Slides.pdf, page 1' in user and 'TP1.pdf' not in user
    event = next(e for e in result.trace if e['event'] == 'tp_text')
    assert event['in_prompt'] and event['pages'] == 2 and event['left_out'] == 1
    assert [c['evidence_id'] for c in result.citations] == ['E1']


@pytest.mark.asyncio
async def test_a_zero_limit_puts_no_tp_text_in_the_prompt():
    from backend.app.agent.search import TP_HEADING
    tools = TwoFiles()
    model = Script('Voir [E1].')
    result = await SearchRunner(CourseTools(tools), model, tp_chars=0).run('On fait quoi ?')
    assert TP_HEADING not in model.seen[0][0]['content']
    event = next(e for e in result.trace if e['event'] == 'tp_text')
    assert not event['in_prompt'] and event['left_out'] == 1
    # As before over the limit: the best TP pages of a search come with the question.
    assert '--- [This TP] TP1.pdf, page 1 ---' in model.seen[0][-1]['content']


def test_the_tp_limit_is_a_share_of_the_model_context():
    from backend.app.agent.tp_text import tp_char_budget
    assert tp_char_budget(131072, 30) == 39321 * 3
    assert tp_char_budget(None, 30) == tp_char_budget(65536, 30)
    assert tp_char_budget(131072, 0) == 0


@pytest.mark.asyncio
async def test_the_model_context_is_read_once_and_kept():
    from backend.app.services import model_servers
    calls, now = [], [0.0]

    async def fetch(url, key):
        calls.append(url)
        return {'data': [{'id': 'flash-next', 'max_model_len': 131072}]}

    model_servers._contexts.clear()
    get = lambda: model_servers.context_tokens('http://m.invalid:8000/v1', 'flash-next', fetch=fetch,
                                               clock=lambda: now[0])
    assert await get() == 131072 and await get() == 131072 and len(calls) == 1
    now[0] = model_servers.CONTEXT_TTL + 1
    assert await get() == 131072 and len(calls) == 2
    model_servers._contexts.clear()


def test_plan_files_takes_whole_files_shortest_first_within_both_limits():
    from backend.app.agent.tp_text import plan_files
    files = [('slides', 70, 140000, 'CM.pdf'), ('tp', 11, 19000, 'TP1.pdf'), ('fiche', 3, 9000, 'fiche.pdf'),
             ('big', 30, 200000, 'B.pdf')]
    assert plan_files(files, 118000) == ['fiche', 'tp']
    assert plan_files(files, 400000) == ['fiche', 'tp', 'big', 'slides']
    # The page cap leaves the longest file unread even when characters would fit.
    assert plan_files(files, 10**9, max_pages=50) == ['fiche', 'tp', 'big']
    assert plan_files(files, 0) == []
