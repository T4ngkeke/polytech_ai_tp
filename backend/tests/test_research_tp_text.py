"""test_research_tp_text.py — Research with the TP text in the system message (RESEARCH_TP_TEXT on)."""
import pytest

from backend.app.agent.course_tools import CourseTools
from backend.app.agent.runner import RESEARCH_TP_NOTE, ResearchRunner
from backend.app.services.research_service import LIMIT_KEYS, ResearchLimits
from backend.tests.test_runner import Script
from backend.tests.test_search_mode import TwoFiles


@pytest.mark.asyncio
async def test_the_tp_is_in_the_prompt_cited_and_free_of_the_budget():
    model = Script('Il faut écrire le programme demandé [E1].', 'DONE')
    tools = CourseTools(TwoFiles(), max_tool_calls=2)
    result = await ResearchRunner(tools, model, max_tool_calls=2, tp_chars=36_000).run('On fait quoi ?')
    system = model.seen[0][0]['content']
    assert RESEARCH_TP_NOTE in system and 'TP1.pdf' in system and '[E1]' in system
    # The TP pages were read before the model and its answer cites one.
    assert result.citations and result.citations[0]['evidence_id'] == 'E1'
    assert any(e.get('event') == 'tp_text' and e['in_prompt'] for e in result.trace)
    # The two free reads leave the two research calls: the model still has its tools.
    assert model.with_tools[0]


@pytest.mark.asyncio
async def test_off_by_default():
    model = Script('Bonjour.', 'DONE')
    await ResearchRunner(CourseTools(TwoFiles()), model).run('On fait quoi ?')
    assert RESEARCH_TP_NOTE not in model.seen[0][0]['content']
    assert ResearchLimits().research_tp_text == 0 and LIMIT_KEYS['RESEARCH_TP_TEXT'] == ('research_tp_text', 0, 1)


@pytest.mark.asyncio
async def test_a_reply_without_research_is_still_checked_with_the_tp_text():
    """Seen on dev 2026-11-21: with the TP pages counted as reads, 'I will check the TP'
    went out as the answer. The pages read for the TP text are not research."""
    from backend.app.agent.result import course_use
    model = Script('Je vais vérifier le contenu du TP1.', 'Il faut tracer des figures [E1].')
    result = await ResearchRunner(CourseTools(TwoFiles()), model, tp_chars=36_000).run('On fait quoi ?')
    assert result.answer.startswith('Il faut tracer') and result.citations
    assert model.purposes[1] == 'answer_check'
    plain = Script('Bonjour !', 'DONE')
    greeting = await ResearchRunner(CourseTools(TwoFiles()), plain, tp_chars=36_000).run('Salut')
    assert greeting.tool_calls == 0 and course_use(greeting) == 'none'
