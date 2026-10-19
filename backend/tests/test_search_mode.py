"""Search mode: one search, the found and adjacent pages, one answer."""
import json

import pytest

from backend.app.agent.course_tools import CourseTools
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
        return ModelReply(text, [], 'ornith', 100, 10)


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
    assert model.seen[1][-1]['content'] == CHECK_TURN
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
    assert model.seen[0][-1]['content'] == 'Dijkstra ?\n\n' + NO_PAGES
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
