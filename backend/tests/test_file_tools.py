"""The student's long attached files (agent/tools/files.py): read on from the
cut, find a passage, and the loop offers the tools only when a file was cut."""
import json

import pytest

from backend.app.agent.runner import step_of
from backend.app.agent.tools.files import FILE_TOOL_NAMES, READ_CHARS, AttachedText, FileTools

TEXT = ''.join(f'Ligne {n}: le théorème {n % 7} dit que tout va bien.\n' for n in range(1, 2001))


def tools(**kw):
    return FileTools({'f1': AttachedText('cours.pdf', TEXT, len(TEXT))}, **kw)


@pytest.mark.asyncio
async def test_read_file_reads_on_from_an_offset_in_whole_lines():
    t = tools()
    text = await t.call('read_file', json.dumps({'file': 'f1', 'start': 20000}))
    head, body = text.split('\n', 1)
    assert head.startswith('f1 · cours.pdf · characters 20000-') and head.endswith(f'of {len(TEXT)}')
    end = int(head.split('characters 20000-')[1].split(' ')[0])
    assert 20000 < end <= 20000 + READ_CHARS and TEXT[end - 1] == '\n'
    assert body.startswith(TEXT[20000:end]) and f'read_file(file="f1", start={end})' in body
    last = await t.call('read_file', json.dumps({'file': 'f1', 'start': len(TEXT) - 100}))
    assert last.endswith('[end of the file]')
    assert step_of('read_file', '{"file": "f1"}', text) == {'tool': 'read_file', 'file': 'cours.pdf', 'ok': True}


@pytest.mark.asyncio
async def test_grep_file_gives_offsets_to_read_from():
    t = tools()
    text = await t.call('grep_file', json.dumps({'file': 'f1', 'pattern': 'Ligne 1234:'}))
    lines = text.splitlines()
    assert lines[0].startswith('1 lines of f1 · cours.pdf')
    offset = int(lines[1].split(':')[0])
    assert TEXT[offset:].startswith('Ligne 1234:')
    many = await t.call('grep_file', json.dumps({'file': 'f1', 'pattern': 'théorème 3'}))
    assert many.endswith('use a narrower pattern') and len(many.splitlines()) == 22
    # An invalid regular expression is searched as words.
    assert 'no line of f1' in await t.call('grep_file', json.dumps({'file': 'f1', 'pattern': 'f(x'}))
    assert step_of('grep_file', '{"pattern": "Ligne"}', text) == {'tool': 'grep_file', 'query': 'Ligne', 'ok': True}


@pytest.mark.asyncio
async def test_errors_and_the_budget():
    t = tools(max_calls=3)
    assert "unknown file 'f9'" in await t.call('read_file', json.dumps({'file': 'f9'}))
    assert 'invalid_arguments' in await t.call('read_file', json.dumps({'start': 5}))
    assert 'past the end' in await t.call('read_file', json.dumps({'file': 'f1', 'start': 10 ** 7}))
    assert 'At most 3 file reads' in await t.call('read_file', json.dumps({'file': 'f1'}))


@pytest.mark.asyncio
async def test_a_file_cut_when_it_was_stored_says_so():
    t = FileTools({'f1': AttachedText('gros.txt', 'abc\n', 900_000)})
    text = await t.call('read_file', json.dumps({'file': 'f1', 'start': 0}))
    assert 'only the first 4 were kept when it was uploaded' in text


class Offered:
    """A scripted model that also records the tool names it was offered."""

    def __init__(self, *steps):
        from backend.tests.test_runner import Script
        self.script, self.offered = Script(*steps), []

    async def __call__(self, messages, max_output, timeout, *, tools=None, purpose=''):
        self.offered.append({t['function']['name'] for t in tools or []})
        return await self.script(messages, max_output, timeout, tools=tools, purpose=purpose)


@pytest.mark.asyncio
async def test_the_loop_reads_a_long_file_only_when_one_was_cut():
    from backend.app.agent.runner import ResearchRunner
    from backend.app.agent.tools.course import CourseTools
    from backend.tests.test_course_tools import ViewFixture
    steps = []
    model = Offered([('read_file', {'file': 'f1', 'start': 20000})], 'La ligne 450 dit que tout va bien.')
    result = await ResearchRunner(CourseTools(ViewFixture()), model, on_step=steps.append,
                                  file_tools=tools()).run('Que dit la ligne 450 ?')
    assert FILE_TOOL_NAMES <= model.offered[0]
    assert 'read_file reads its other parts' in model.script.seen[0][0]['content']
    tool_reply = next(m for m in model.script.seen[1] if m['role'] == 'tool')['content']
    assert tool_reply.startswith('f1 · cours.pdf · characters 20000-') and 'Ligne 450:' in tool_reply
    assert {'tool': 'read_file', 'file': 'cours.pdf', 'ok': True} in steps
    assert 'tout va bien' in result.answer
    # Without a long file, neither the tools nor the note.
    model = Offered('Réponse.')
    await ResearchRunner(CourseTools(ViewFixture()), model).run('Question ?')
    assert not FILE_TOOL_NAMES & model.offered[0]
    assert 'read_file' not in model.script.seen[0][0]['content']
