"""test_task_tools.py — the task checklist (ported from Open WebUI)."""
import json

import pytest

from backend.app.agent.tools.course import CourseTools
from backend.app.agent.runner import ResearchRunner
from backend.app.agent.tools.tasks import MAX_CALLS, MAX_TASKS, TASK_TOOL_NAMES, TaskList
from backend.tests.test_course_tools import ViewFixture
from backend.tests.test_runner import Script


def create(items):
    return json.dumps({'tasks': items})


def test_create_then_update_as_in_open_webui():
    seen = []
    tasks = TaskList(seen.append)
    out = json.loads(tasks.call('create_tasks', create([{'content': 'Trouver la définition'},
                                                        {'content': 'Lire l\'exemple', 'id': 'ex'}])))
    assert [t['id'] for t in out['tasks']] == ['1', 'ex'] and out['summary']['pending'] == 2
    out = json.loads(tasks.call('update_task', json.dumps({'id': 'ex', 'status': 'in_progress'})))
    assert out['summary'] == {'total': 2, 'pending': 1, 'in_progress': 1, 'completed': 0, 'cancelled': 0}
    out = json.loads(tasks.call('update_task', json.dumps({'id': '1'})))          # default: completed
    assert out['tasks'][0]['status'] == 'completed'
    assert len(seen) == 3 and seen[-1][0]['status'] == 'completed'


def test_errors_are_returned_to_the_model():
    tasks = TaskList()
    tasks.call('create_tasks', create([{'content': 'A'}]))
    assert 'Invalid status' in tasks.call('update_task', json.dumps({'id': '1', 'status': 'done'}))
    assert 'not found' in tasks.call('update_task', json.dumps({'id': '9'}))
    assert 'error' in json.loads(tasks.call('create_tasks', '{"tasks": "nope"}'))


def test_the_list_and_the_calls_are_limited():
    tasks = TaskList()
    out = json.loads(tasks.call('create_tasks', create([{'content': 'x' * 500}] * 20)))
    assert len(out['tasks']) == MAX_TASKS and len(out['tasks'][0]['content']) == 200
    for _ in range(MAX_CALLS):
        tasks.call('update_task', json.dumps({'id': '1'}))
    assert 'At most' in tasks.call('update_task', json.dumps({'id': '1'}))


@pytest.mark.asyncio
async def test_the_runner_keeps_the_list_outside_the_course_budget():
    events = []
    model = Script([('create_tasks', {'tasks': [{'content': 'Chercher'}, {'content': 'Lire'}]})],
                   [('search', {'query': 'produit cartésien'})],
                   [('read', {'page': 'd1.p10'}), ('update_task', {'id': '1'})],
                   'Réponse [E1].')
    result = await ResearchRunner(CourseTools(ViewFixture()), model, max_tool_calls=2,
                                  on_tasks=events.append).run('Produit cartésien ?')
    # Two course tool calls fill the budget of two; the task calls do not count.
    assert result.tool_calls == 2 and [c['evidence_id'] for c in result.citations] == ['E1']
    assert result.tasks == [{'id': '1', 'content': 'Chercher', 'status': 'completed'},
                            {'id': '2', 'content': 'Lire', 'status': 'pending'}]
    assert len(events) == 2


class Offered(Script):
    """Records the tool names offered at each call."""

    async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
        self.names = getattr(self, 'names', []) + [{t['function']['name'] for t in tools or []}]
        return await super().__call__(messages, max_output, timeout, tools=tools, purpose=purpose)


@pytest.mark.asyncio
async def test_the_task_tools_are_offered_with_the_course_tools():
    model = Offered('Bonjour.')
    await ResearchRunner(CourseTools(ViewFixture()), model).run('Bonjour')
    assert TASK_TOOL_NAMES <= model.names[0] and 'search' in model.names[0]


@pytest.mark.asyncio
async def test_the_list_can_be_left_out():
    model = Offered('Bonjour.')
    result = await ResearchRunner(CourseTools(ViewFixture()), model, offer_tasks=False).run('Bonjour')
    assert result.tasks is None and not TASK_TOOL_NAMES & model.names[0]
