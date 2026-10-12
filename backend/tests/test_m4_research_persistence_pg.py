"""M4-C lifecycle and accounting against disposable PostgreSQL/pgvector."""
import asyncio
import json
import uuid
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.agent.model import ModelReply
from backend.app.agent.result import ResearchResult, ResearchRunStopped
from backend.app.agent.tools import create_scope
from backend.app.main import app
from backend.app.models import (
    AgentRun, ClassStudent, Document, DocumentBlock, DocumentPage,
    Message, MessageCitation, ModelCallUsage, Session, UsageReservation,
    UsageStat, User,
)
from backend.app.routers.teacher import get_storage_root
from backend.app.services.document_cleanup import mark_deleted
from backend.app.services.research_runs import (
    STALE_RUN_SECONDS, CitationInvalid, ResearchRuns, RunConflict, _label_order,
)
from backend.app.services.research_usage import ResearchCallConflict, ResearchQuotaExceeded, ResearchUsage
from backend.tests.conftest import make_user
from backend.tests.test_m4_tools_pg import _indexed_pdf
from backend.tests.test_structured_ingest import seed
from backend.app.models import _utcnow


def scripted(*steps, log=None):
    """A model double: each step is an answer text or a list of (tool, arguments)."""
    steps = list(steps)

    async def model(messages, max_output, timeout, *, tools=None, purpose=''):
        if log is not None:
            log.append(purpose)
        step = steps.pop(0)
        if isinstance(step, str):
            return ModelReply(step, [], 'fake', 30, 10)
        return ModelReply('', [{'id': f'call{len(steps)}{n}', 'name': name, 'arguments': json.dumps(args)}
                               for n, (name, args) in enumerate(step)], 'fake', 30, 10)
    return model


async def _setup(db, *, quota=500):
    teacher, _, lab, _ = await seed(db)
    student = make_user(quota=quota)
    db.add(student)
    await db.flush()
    db.add(ClassStudent(class_id=lab.class_id, student_id=student.id))
    session = Session(id=uuid.uuid4(), lab_id=lab.id, user_id=student.id)
    db.add(session)
    await db.commit()
    scope = await create_scope(db, user_id=student.id, session_id=session.id)
    factory = async_sessionmaker(db.bind, expire_on_commit=False)
    return scope, factory


@pytest.mark.asyncio
async def test_run_idempotency_active_session_and_unique_user_message(pg_session):
    scope, factory = await _setup(pg_session)
    store = ResearchRuns(factory)
    first = await store.begin_run(scope=scope, question='Where?', client_request_id='req-1')
    again = await store.begin_run(scope=scope, question='Where?', client_request_id='req-1')
    assert first.created and not again.created and first.run_id == again.run_id
    assert await pg_session.scalar(select(func.count()).select_from(Message)) == 1
    with pytest.raises(RunConflict, match='active_session_run'):
        await store.begin_run(scope=await create_scope(pg_session,
            user_id=scope.user_id, session_id=scope.session_id),
            question='Different?', client_request_id='req-2')
    with pytest.raises(RunConflict, match='request_id_payload_conflict'):
        await store.begin_run(scope=scope, question='Changed?', client_request_id='req-1')
    assert await store.mark_terminal(first.run_id, status='failed',
                                     stop_reason='test_failure') == 'failed'
    new = await store.begin_run(scope=await create_scope(pg_session,
        user_id=scope.user_id, session_id=scope.session_id),
        question='Different?', client_request_id='req-2')
    assert new.created
    stat = await pg_session.scalar(select(UsageStat).where(UsageStat.user_id == scope.user_id))
    await pg_session.refresh(stat)
    assert stat.request_count == 2


@pytest.mark.asyncio
async def test_stale_active_run_does_not_block_the_session(pg_session):
    scope, factory = await _setup(pg_session)
    store = ResearchRuns(factory)
    lost = await store.begin_run(scope=scope, question='Lost?', client_request_id='lost-1')
    async with factory() as db:
        async with db.begin():
            run = await db.get(AgentRun, lost.run_id)
            run.started_at = _utcnow() - timedelta(seconds=STALE_RUN_SECONDS + 1)
    new = await store.begin_run(scope=await create_scope(pg_session,
        user_id=scope.user_id, session_id=scope.session_id),
        question='Again?', client_request_id='lost-2')
    assert new.created
    async with factory() as db:
        run = await db.get(AgentRun, lost.run_id)
        assert (run.status, run.stop_reason, run.settlement_status) == ('failed', 'worker_lost', 'settled')


def test_citations_sort_by_evidence_number():
    rows = [MessageCitation(label=x) for x in ('E10', 'E2', 'E1')]
    assert [c.label for c in sorted(rows, key=_label_order)] == ['E1', 'E2', 'E10']


@pytest.mark.asyncio
async def test_session_reload_queries_do_not_grow_with_citations(pg_session, tmp_path, monkeypatch):
    """Reloading a session checks access once per source, not once per citation."""
    from sqlalchemy import event
    try:
        db = pg_session
        doc, rev, _, student, _, chat, _, _ = await _indexed_pdf(db, tmp_path, monkeypatch)
        scope = await create_scope(db, user_id=student.id, session_id=chat.id)
        factory = async_sessionmaker(db.bind, expire_on_commit=False)
        runs = ResearchRuns(factory)
        block = await db.scalar(select(DocumentBlock).where(
            DocumentBlock.revision_id == rev.id, DocumentBlock.content != '',
            DocumentBlock.kind == 'text').limit(1))
        page = await db.get(DocumentPage, (rev.id, block.page_id))
        def cite(label, end):
            return {'evidence_id': label, 'document_id': str(doc.id), 'revision_id': str(rev.id),
                    'page_id': str(page.page_id), 'pdf_page_no': page.preview_page_no,
                    'block_id': str(block.block_id), 'range': {'start': 0, 'end': end},
                    'origin': (block.source_ref or {}).get('content_origin', 'native')}
        async def answers(count):
            for n in range(count):
                started = await runs.begin_run(scope=replace(scope, run_id=uuid.uuid4()),
                    question=f'Q{n}?', client_request_id=f'reload-{n}-{count}')
                labels = [f'E{3 * n + k}' for k in (1, 2, 3)]
                await runs.finish_run(started.run_id, ResearchResult(
                    'Voir ' + ' '.join(f'[{x}]' for x in labels) + '.',
                    [cite(x, 5 + k) for k, x in enumerate(labels)], 'finished', [], 1, 1))
        statements = []
        def count(*_args):
            statements.append(1)
        async def queries(load):
            statements.clear()
            event.listen(db.bind.sync_engine, 'before_cursor_execute', count)
            try:
                result = await load()
            finally:
                event.remove(db.bind.sync_engine, 'before_cursor_execute', count)
            return result, len(statements)
        reload = lambda: runs.load_session_citations(session_id=chat.id, user_id=student.id)
        history = lambda: runs.load_safe_history(scope)
        await answers(2)
        _, reload_small = await queries(reload)
        _, history_small = await queries(history)
        await answers(4)
        restored, reload_large = await queries(reload)
        kept, history_large = await queries(history)
        assert len(restored) == 6 and all(len(v) == 3 and v[0]['available'] for v in restored.values())
        assert sum(m['role'] == 'assistant' for m in kept) == 6
        print('QUERIES', {'reload': (reload_small, reload_large), 'history': (history_small, history_large)})
        # Four more answers (12 citations of the same source) add no queries.
        assert reload_large == reload_small and history_large == history_small
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_concurrent_quota_reservation_idempotent_settlement_and_unknown(pg_session):
    scope, factory = await _setup(pg_session, quota=100)
    run = await ResearchRuns(factory).begin_run(scope=scope,
        question='Quota?', client_request_id='quota')
    usage = ResearchUsage(factory)
    async def reserve():
        return await usage.reserve(run_id=run.run_id, purpose='agent', model='local',
            input_tokens=20, max_output_tokens=50, alpha=1, beta=1)
    outcomes = await asyncio.gather(reserve(), reserve(), return_exceptions=True)
    succeeded = [x for x in outcomes if isinstance(x, uuid.UUID)]
    assert len(succeeded) == 1
    assert sum(isinstance(x, ResearchQuotaExceeded) for x in outcomes) == 1
    call_id = succeeded[0]
    assert await usage.reserve(run_id=run.run_id, purpose='agent', model='local',
        input_tokens=20, max_output_tokens=50, alpha=1, beta=1,
        call_id=call_id) == call_id
    with pytest.raises(ResearchCallConflict):
        await usage.reserve(run_id=run.run_id, purpose='agent', model='local',
            input_tokens=21, max_output_tokens=50, alpha=1, beta=1,
            call_id=call_id)
    first = await usage.settle(call_id=call_id, input_tokens=20,
        output_tokens=10, status='ok', elapsed_seconds=.1, usage_source='actual')
    second = await usage.settle(call_id=call_id, input_tokens=20,
        output_tokens=10, status='ok', elapsed_seconds=.1, usage_source='actual')
    assert first.call_id == second.call_id and first.billed_tokens == 30
    another = await reserve()  # 30 billed + 70 held = 100
    async with factory() as db:
        async with db.begin():
            row = await db.get(UsageReservation, another)
            row.expires_at = _utcnow() - timedelta(seconds=1)
    assert await usage.mark_expired_unknown(another)
    assert not await usage.mark_expired_unknown(another)
    with pytest.raises(ResearchQuotaExceeded):
        await reserve()  # unknown is still held; it is not silently free
    await usage.settle(call_id=another, input_tokens=20, output_tokens=0,
                       status='reconciled', elapsed_seconds=.2,
                       usage_source='estimated')
    async with factory() as db:
        stat = await db.scalar(select(UsageStat).where(UsageStat.user_id == scope.user_id))
        rows = (await db.scalars(select(ModelCallUsage))).all()
        assert stat.tokens_used == 50 and len(rows) == 2
        assert rows[0].token_alpha == rows[1].token_alpha == 1


@pytest.mark.asyncio
async def test_model_wrapper_estimates_missing_usage_and_failed_call(pg_session):
    scope, factory = await _setup(pg_session, quota=1000)
    run = await ResearchRuns(factory).begin_run(scope=scope,
        question='Usage?', client_request_id='usage')
    usage = ResearchUsage(factory)
    async def no_usage(messages, max_output, timeout, **kwargs):
        return ModelReply('A useful answer [E1].', model='local')
    wrapped = usage.wrap_model(run_id=run.run_id, model='local', alpha=.2, beta=1, call=no_usage)
    await wrapped([{'role': 'user', 'content': 'A long enough question for estimation'}], 100, 2,
                  purpose='answer')
    async def broken(messages, max_output, timeout, **kwargs):
        raise RuntimeError('model_failed')
    with pytest.raises(RuntimeError):
        await usage.wrap_model(run_id=run.run_id, model='local', alpha=.2, beta=1, call=broken)(
            [{'role': 'user', 'content': 'Another question'}], 100, 2,
            tools=[{'type': 'function', 'function': {'name': 'search'}}])
    await usage.record_non_token_call(run_id=run.run_id, purpose='rerank',
                                      model='local-reranker', elapsed_seconds=.4)
    async with factory() as db:
        calls = (await db.scalars(select(ModelCallUsage).order_by(ModelCallUsage.created_at))).all()
        assert len(calls) == 3
        assert [x.purpose for x in calls] == ['answer', 'agent', 'rerank']
        assert {x.usage_source for x in calls} == {'estimated', 'none'}
        assert all(x.input_tokens != 10 for x in calls[:2])
        assert next(x for x in calls if x.purpose == 'rerank').billed_tokens == 0
        assert next(x for x in calls if x.status == 'failed').billed_tokens > 0


@pytest.mark.asyncio
async def test_atomic_answer_citations_reload_and_delete_tombstone(pg_session, tmp_path, monkeypatch):
    try:
        db = pg_session
        doc, rev, teacher, student, _, chat, _, embed = await _indexed_pdf(
            db, tmp_path, monkeypatch)
        scope = await create_scope(db, user_id=student.id, session_id=chat.id)
        factory = async_sessionmaker(db.bind, expire_on_commit=False)
        runs = ResearchRuns(factory)
        started = await runs.begin_run(scope=scope, question='Definition?',
                                       client_request_id='citation')
        block = await db.scalar(select(DocumentBlock).where(
            DocumentBlock.revision_id == rev.id, DocumentBlock.content != '',
            DocumentBlock.kind == 'text').limit(1))
        page = await db.get(DocumentPage, (rev.id, block.page_id))
        citation = {'evidence_id': 'E1', 'document_id': str(doc.id),
                    'revision_id': str(rev.id), 'page_id': str(page.page_id),
                    'pdf_page_no': page.preview_page_no, 'block_id': str(block.block_id),
                    'range': {'start': 0, 'end': min(20, len(block.content))},
                    'origin': (block.source_ref or {}).get('content_origin', 'native')}
        result = ResearchResult('See the definition [E1].', [citation], 'finished',
                                [], 1, 2)
        message_id = await runs.finish_run(started.run_id, result)
        assert await runs.finish_run(started.run_id, result) == message_id
        loaded = await runs.load_result(run_id=started.run_id, user_id=student.id)
        assert loaded['answer'] == result.answer
        assert len(loaded['citations']) == 1
        assert loaded['citations'][0]['available']
        assert f'page={page.preview_page_no}' in loaded['citations'][0]['href']
        restored = await runs.load_session_citations(
            session_id=scope.session_id, user_id=student.id)
        assert restored[message_id][0]['citation_id'] == loaded['citations'][0]['citation_id']
        assert any(m['role'] == 'assistant' for m in await runs.load_safe_history(scope))
        async with factory() as check:
            assert await check.scalar(select(func.count()).select_from(MessageCitation)) == 1
            assert await check.scalar(select(func.count()).select_from(Message)) == 2
        await db.refresh(doc)
        await db.refresh(teacher)
        await mark_deleted(db, document_id=doc.id, teacher_id=teacher.id,
                           expected_generation=doc.generation)
        await db.commit()
        after = await runs.load_result(run_id=started.run_id, user_id=student.id)
        assert after['answer'] == result.answer
        assert not after['citations'][0]['available'] and after['citations'][0]['href'] is None
        revoked_history = await runs.load_session_citations(
            session_id=scope.session_id, user_id=student.id)
        assert revoked_history[message_id][0]['href'] is None
        assert all(m['role'] != 'assistant' for m in await runs.load_safe_history(scope))
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_invalid_citation_rolls_back_answer(pg_session):
    scope, factory = await _setup(pg_session)
    runs = ResearchRuns(factory)
    started = await runs.begin_run(scope=scope, question='Find?', client_request_id='bad-citation')
    fake = {'evidence_id': 'E1', 'document_id': str(uuid.uuid4()),
            'revision_id': str(uuid.uuid4()), 'page_id': str(uuid.uuid4()),
            'pdf_page_no': 1, 'block_id': str(uuid.uuid4()),
            'range': {'start': 0, 'end': 1}, 'origin': 'native'}
    with pytest.raises(CitationInvalid):
        await runs.finish_run(started.run_id, ResearchResult('Wrong [E1].',
            [fake], 'finished', [], 1, 1))
    async with factory() as db:
        run = await db.get(AgentRun, started.run_id)
        assert run.status == 'running' and run.assistant_message_id is None
        assert await db.scalar(select(func.count()).select_from(Message)) == 1


@pytest.mark.asyncio
async def test_cancel_stops_calls_and_replay_never_reopens(pg_session):
    scope, factory = await _setup(pg_session)
    runs, usage = ResearchRuns(factory), ResearchUsage(factory)
    started = await runs.begin_run(scope=scope, question='Cancel?',
                                   client_request_id='cancel-1')
    assert await runs.request_cancel(started.run_id, scope.user_id) == 'cancelling'
    with pytest.raises(Exception, match='run_not_running'):
        await usage.reserve(run_id=started.run_id, purpose='agent', model='local',
            input_tokens=10, max_output_tokens=10, alpha=.2, beta=1)
    assert await runs.mark_terminal(started.run_id, status='cancelled',
                                    stop_reason='client_cancelled') == 'cancelled'
    replay = await runs.begin_run(scope=scope, question='Cancel?',
                                  client_request_id='cancel-1')
    assert not replay.created and replay.status == 'cancelled'
    assert (await runs.load_result(run_id=started.run_id,
                                   user_id=scope.user_id))['answer'] is None


@pytest.mark.asyncio
async def test_failed_run_with_unknown_call_reconciles_later(pg_session):
    scope, factory = await _setup(pg_session)
    runs, usage = ResearchRuns(factory), ResearchUsage(factory)
    started = await runs.begin_run(scope=scope, question='Crash?',
                                   client_request_id='crash-1')
    call_id = await usage.reserve(run_id=started.run_id, purpose='agent', model='local',
        input_tokens=20, max_output_tokens=40, alpha=1, beta=1)
    async with factory() as db:
        async with db.begin():
            row = await db.get(UsageReservation, call_id)
            row.expires_at = _utcnow() - timedelta(seconds=1)
    assert await usage.mark_expired_unknown(call_id)
    await runs.mark_terminal(started.run_id, status='failed',
                             stop_reason='worker_lost')
    async with factory() as db:
        run = await db.get(AgentRun, started.run_id)
        assert run.settlement_status == 'open'
    await usage.settle(call_id=call_id, input_tokens=20, output_tokens=7,
                       elapsed_seconds=1, status='reconciled', usage_source='actual')
    async with factory() as db:
        run = await db.get(AgentRun, started.run_id)
        assert run.settlement_status == 'settled'


@pytest.mark.asyncio
async def test_persisted_orchestrator_is_idempotent_and_save_failure_never_returns(pg_session):
    from backend.app.agent.tools import ResearchScope
    from backend.app.services.research_service import run_persisted_research
    scope, factory = await _setup(pg_session, quota=5000)
    calls = []

    class NoTools:
        def __init__(self, bound_scope):
            self.scope = bound_scope
        async def _check_session(self):
            pass
        async def _revision(self, document_id):
            pass
        async def page_index(self):
            return {}
        async def execute(self, name, arguments):
            raise AssertionError('no tools expected')

    async def clarify(messages, max_output, timeout, **kwargs):
        calls.append(kwargs['purpose'])
        return ModelReply('Which course do you mean?', model='fake', input_tokens=42, output_tokens=13)
    kwargs = dict(scope=scope, question='Which?', client_request_id='orchestrated',
        model=clarify, model_name='fake',
        token_alpha=.2, token_beta=1, session_factory=factory,
        tool_factory=NoTools)
    first = await run_persisted_research(**kwargs)
    second = await run_persisted_research(**kwargs)
    assert first == second and first['status'] == 'finished'
    assert calls == ['agent']
    async with factory() as db:
        run = await db.get(AgentRun, uuid.UUID(first['run_id']))
        assistant = await db.get(Message, run.assistant_message_id)
        assert assistant.billed_tokens == 21  # round(42*.2+13)
        assert await db.scalar(select(func.count()).select_from(Message)) == 2
        assert await db.scalar(select(func.count()).select_from(ModelCallUsage)) == 1

    # The model can cite a fake but syntactically valid reader item. The final
    # DB check rejects it, so no successful result escapes the orchestrator.
    bad_doc, bad_rev, bad_page, bad_block = (uuid.uuid4() for _ in range(4))
    forged_scope = ResearchScope(scope.user_id, scope.session_id, uuid.uuid4(),
        scope.class_id, scope.lab_id, {bad_doc: bad_rev})
    class ForgedTools(NoTools):
        async def page_index(self):
            return {str(bad_doc): {'filename': 'Forged.pdf',
                                   'pages': [{'page_id': str(bad_page), 'pdf_page_no': 1}]}}
        async def execute(self, name, arguments):
            return {'ok': True, 'items': [{'block_id': str(bad_block),
                'page': {'page_id': str(bad_page), 'preview_page_no': 1},
                'content': 'forged', 'range': {'start': 0, 'end': 6},
                'block_length': 6, 'type': 'text',
                'source_completeness': 'complete', 'issues': [],
                'source': {'content_origin': 'native'}, 'assets': []}],
                'next_cursor': None}
    with pytest.raises(CitationInvalid):
        await run_persisted_research(scope=forged_scope, question='Forged?',
            client_request_id='forged',
            model=scripted([('read', {'page': 'd1.p1'})], 'Forged [E1].'), model_name='fake',
            token_alpha=.2, token_beta=1, session_factory=factory,
            tool_factory=ForgedTools)
    async with factory() as db:
        failed = await db.scalar(select(AgentRun).where(AgentRun.client_request_id == 'forged'))
        assert failed.status == 'failed' and failed.assistant_message_id is None
        assert await db.scalar(select(func.count()).select_from(Message)) == 3
        assert await db.scalar(select(func.count()).select_from(ModelCallUsage)) == 3


@pytest.mark.asyncio
async def test_quota_denial_propagates_and_does_not_call_model(pg_session):
    from backend.app.services.research_service import run_persisted_research
    scope, factory = await _setup(pg_session, quota=10)
    invoked = False
    async def model(messages, max_output, timeout, **kwargs):
        nonlocal invoked
        invoked = True
        return ModelReply('{}')
    with pytest.raises(ResearchQuotaExceeded):
        await run_persisted_research(scope=scope, question='No quota?',
            client_request_id='no-quota', model=model, model_name='local',
            token_alpha=1, token_beta=1, session_factory=factory)
    assert not invoked
    async with factory() as db:
        run = await db.scalar(select(AgentRun).where(
            AgentRun.client_request_id == 'no-quota'))
        assert run.status == 'failed' and run.assistant_message_id is None
        assert await db.scalar(select(func.count()).select_from(ModelCallUsage)) == 0


@pytest.mark.asyncio
async def test_m4c_sql_migration_repeatable_after_legacy_tables(pg_session):
    from sqlalchemy import text
    from scripts.migrate_document_snapshots import M4C_SQL_PATH, M4C_VERSION, _apply_one
    from backend.app.models import (
        AgentRun, MessageCitation, SessionReadState, ModelCallUsage, UsageReservation,
    )
    await pg_session.commit()
    async with pg_session.bind.begin() as conn:
        for model in (UsageReservation, ModelCallUsage, MessageCitation,
                      SessionReadState, AgentRun):
            await conn.run_sync(lambda sync, table=model.__table__: table.drop(sync))
        await conn.execute(text('CREATE TABLE IF NOT EXISTS document_schema_migrations '
            '(version TEXT PRIMARY KEY, checksum TEXT NOT NULL, '
            'applied_at TIMESTAMPTZ NOT NULL DEFAULT now())'))
        await conn.execute(text('DELETE FROM document_schema_migrations WHERE version=:v'),
                           {'v': M4C_VERSION})  # disposable _test DB only
        assert await _apply_one(conn, M4C_VERSION, M4C_SQL_PATH)
        assert not await _apply_one(conn, M4C_VERSION, M4C_SQL_PATH)
        for table in ('agent_runs','message_citations','session_read_state',
                      'model_call_usage','usage_reservations'):
            assert await conn.scalar(text('SELECT to_regclass(:name) IS NOT NULL'),
                                     {'name': table})


@pytest.mark.asyncio
async def test_external_cancel_prevents_next_tool_and_settles_inflight_call(pg_session):
    from backend.app.services.research_service import run_persisted_research
    scope, factory = await _setup(pg_session, quota=5000)
    entered, release = asyncio.Event(), asyncio.Event()
    async def model(messages, max_output, timeout, **kwargs):
        entered.set()
        await release.wait()
        return ModelReply('', [{'id': 'c1', 'name': 'search', 'arguments': '{"query": "anything"}'}],
                          'fake', 30, 10)
    task = asyncio.create_task(run_persisted_research(
        scope=scope, question='Cancel during model?', client_request_id='inflight',
        model=model, model_name='fake',
        token_alpha=.2, token_beta=1, session_factory=factory))
    await asyncio.wait_for(entered.wait(), 5)
    assert await ResearchRuns(factory).request_cancel(scope.run_id,
                                                       scope.user_id) == 'cancelling'
    release.set()
    with pytest.raises(ResearchRunStopped):
        await task
    async with factory() as db:
        run = await db.get(AgentRun, scope.run_id)
        assert run.status == 'cancelled' and run.settlement_status == 'settled'
        assert run.assistant_message_id is None
        usage = (await db.scalars(select(ModelCallUsage))).all()
        assert len(usage) == 1 and usage[0].billed_tokens > 0


@pytest.mark.asyncio
async def test_task_cancellation_charges_started_call_and_stops(pg_session):
    from backend.app.services.research_service import run_persisted_research
    scope, factory = await _setup(pg_session, quota=5000)
    entered = asyncio.Event()
    async def waiting_model(messages, max_output, timeout, **kwargs):
        entered.set()
        await asyncio.Event().wait()
        raise AssertionError('unreachable')
    task = asyncio.create_task(run_persisted_research(
        scope=scope, question='Disconnect?', client_request_id='disconnect',
        model=waiting_model, model_name='fake',
        token_alpha=.2, token_beta=1, session_factory=factory))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with factory() as db:
        run = await db.get(AgentRun, scope.run_id)
        assert run.status == 'cancelled' and run.settlement_status == 'settled'
        rows = (await db.scalars(select(ModelCallUsage))).all()
        assert len(rows) == 1 and rows[0].status == 'cancelled'
        assert rows[0].usage_source == 'estimated' and rows[0].billed_tokens > 0


@pytest.mark.asyncio
async def test_repeated_cancellation_still_ends_the_run(pg_session):
    """A closed stream can be cancelled again while the run is cleaning up; the
    run must still reach 'cancelled' instead of staying 'cancelling'."""
    from backend.app.services.research_service import run_persisted_research
    scope, factory = await _setup(pg_session, quota=5000)
    entered = asyncio.Event()
    async def waiting_model(messages, max_output, timeout, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(run_persisted_research(
        scope=scope, question='Closed twice?', client_request_id='closed-twice',
        model=waiting_model, model_name='fake',
        token_alpha=.2, token_beta=1, session_factory=factory))
    await asyncio.wait_for(entered.wait(), 5)
    while not task.done():
        task.cancel()
        await asyncio.sleep(0.005)
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):  # the shielded cleanup may finish just after the task
        async with factory() as db:
            run = await db.get(AgentRun, scope.run_id)
        if run.status == 'cancelled':
            break
        await asyncio.sleep(0.05)
    assert (run.status, run.stop_reason) == ('cancelled', 'client_cancelled')
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_teacher_preview_bills_only_teacher_identity(pg_session):
    teacher, _, lab, _ = await seed(pg_session)
    preview = Session(id=uuid.uuid4(), lab_id=lab.id, user_id=teacher.id,
                      is_test=True)
    pg_session.add(preview)
    await pg_session.commit()
    scope = await create_scope(pg_session, user_id=teacher.id,
                               session_id=preview.id)
    factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
    run = await ResearchRuns(factory).begin_run(scope=scope,
        question='Teacher preview?', client_request_id='preview')
    usage = ResearchUsage(factory)
    call_id = await usage.reserve(run_id=run.run_id, purpose='agent',
        model='teacher-model', input_tokens=40, max_output_tokens=40,
        alpha=.2, beta=1)
    await usage.settle(call_id=call_id, input_tokens=40, output_tokens=10,
        elapsed_seconds=.1, usage_source='actual', status='ok')
    async with factory() as db:
        stats = (await db.scalars(select(UsageStat))).all()
        assert len(stats) == 1 and stats[0].user_id == teacher.id
        assert stats[0].tokens_used == 18


@pytest.mark.asyncio
async def test_membership_revocation_keeps_text_but_removes_live_citation(pg_session, tmp_path, monkeypatch):
    try:
        from sqlalchemy import delete
        doc, rev, teacher, student, _, chat, _, _ = await _indexed_pdf(
            pg_session, tmp_path, monkeypatch)
        scope = await create_scope(pg_session, user_id=student.id,
                                   session_id=chat.id)
        factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
        runs = ResearchRuns(factory)
        started = await runs.begin_run(scope=scope, question='Read?',
                                       client_request_id='revoked')
        block = await pg_session.scalar(select(DocumentBlock).where(
            DocumentBlock.revision_id == rev.id,
            DocumentBlock.content != '').limit(1))
        page = await pg_session.get(DocumentPage, (rev.id, block.page_id))
        evidence = {'evidence_id': 'E1', 'document_id': str(doc.id),
            'revision_id': str(rev.id), 'page_id': str(page.page_id),
            'pdf_page_no': page.preview_page_no,
            'block_id': str(block.block_id),
            'range': {'start': 0, 'end': min(5, len(block.content))},
            'origin': (block.source_ref or {}).get('content_origin', 'native')}
        await runs.finish_run(started.run_id, ResearchResult('Course [E1].',
            [evidence], 'finished', [], 1, 1))
        await pg_session.execute(delete(ClassStudent).where(
            ClassStudent.class_id == doc.class_id,
            ClassStudent.student_id == student.id))
        await pg_session.commit()
        old = await runs.load_result(run_id=started.run_id,
                                     user_id=student.id)
        assert old['answer'] == 'Course [E1].'
        assert not old['citations'][0]['available']
        assert old['citations'][0]['href'] is None
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_visual_asset_citation_has_no_fabricated_text_range(pg_session, tmp_path, monkeypatch):
    try:
        from backend.app.models import DocumentAsset
        doc, rev, _, student, _, chat, _, _ = await _indexed_pdf(
            pg_session, tmp_path, monkeypatch)
        scope = await create_scope(pg_session, user_id=student.id,
                                   session_id=chat.id)
        factory = async_sessionmaker(pg_session.bind, expire_on_commit=False)
        runs = ResearchRuns(factory)
        started = await runs.begin_run(scope=scope, question='What is in image?',
                                       client_request_id='visual')
        page = await pg_session.scalar(select(DocumentPage).where(
            DocumentPage.revision_id == rev.id,
            DocumentPage.preview_asset_id.is_not(None)).limit(1))
        asset = await pg_session.get(DocumentAsset, page.preview_asset_id)
        assert asset.access_class == 'student_visible'
        visual = {'evidence_id': 'E1', 'document_id': str(doc.id),
            'revision_id': str(rev.id), 'page_id': str(page.page_id),
            'pdf_page_no': page.preview_page_no, 'block_id': None,
            'asset_id': str(asset.id), 'range': None, 'origin': 'live_visual'}
        await runs.finish_run(started.run_id, ResearchResult(
            'See image [E1].', [visual], 'finished', [], 1, 1))
        loaded = await runs.load_result(run_id=started.run_id,
                                        user_id=student.id)
        assert loaded['citations'][0]['available']
        assert loaded['citations'][0]['range'] is None
    finally:
        app.dependency_overrides.pop(get_storage_root, None)


@pytest.mark.asyncio
async def test_concurrent_same_request_only_one_run_and_message(pg_session):
    scope, factory = await _setup(pg_session)
    runs = ResearchRuns(factory)
    results = await asyncio.gather(*[
        runs.begin_run(scope=scope, question='Same?', client_request_id='same-id')
        for _ in range(2)
    ])
    assert sum(x.created for x in results) == 1
    assert results[0].run_id == results[1].run_id
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(AgentRun)) == 1
        assert await db.scalar(select(func.count()).select_from(Message)) == 1
        usage = await db.scalar(select(UsageStat).where(
            UsageStat.user_id == scope.user_id))
        assert usage.request_count == 1


@pytest.mark.asyncio
async def test_citation_correction_has_own_model_call_usage_purpose(pg_session):
    from backend.app.services.research_service import run_persisted_research
    scope, factory = await _setup(pg_session, quota=5000)
    purposes = []
    result = await run_persisted_research(scope=scope, question='Which?',
        client_request_id='correction',
        model=scripted('Answer [E9].', 'Which course do you mean?', log=purposes), model_name='fake',
        token_alpha=.2, token_beta=1, session_factory=factory)
    assert result['status'] == 'finished' and purposes == ['agent', 'citation_correction']
    async with factory() as db:
        stored = (await db.scalars(select(ModelCallUsage.purpose)
            .order_by(ModelCallUsage.created_at))).all()
        assert stored == ['agent', 'citation_correction']
        message = await db.get(Message, uuid.UUID(result['message_id']))
        assert message.billed_tokens == 32


@pytest.mark.asyncio
async def test_optional_real_model_usage_is_persisted(pg_session):
    import os
    from pathlib import Path
    from backend.app.agent.model import ResearchModel
    from backend.app.services.model_routing import Endpoint
    from backend.app.services.research_service import run_persisted_research
    output = os.getenv('M4C_REAL_MODEL_OUT')
    if not output:
        pytest.skip('Set M4C_REAL_MODEL_OUT (and M4C_MODEL_URL, M4C_MODEL) for one real model call')
    scope, factory = await _setup(pg_session, quota=10000)
    endpoint = Endpoint(os.getenv('M4C_MODEL_URL', 'http://localhost:11434/v1'), 'local',
                        os.getenv('M4C_MODEL', 'qwen3.6:35b-a3b'))
    model = ResearchModel(endpoint)
    try:
        answer = await run_persisted_research(scope=scope,
            question='What is the exact date of my personal thesis defense in 2027?',
            client_request_id='real-model-usage', model=model, model_name=endpoint.model,
            token_alpha=.2, token_beta=1, session_factory=factory)
    finally:
        await model.client.close()
    async with factory() as db:
        calls = (await db.scalars(select(ModelCallUsage).where(
            ModelCallUsage.run_id == scope.run_id))).all()
        usage = await db.scalar(select(UsageStat).where(
            UsageStat.user_id == scope.user_id))
        assistant = await db.get(Message, uuid.UUID(answer['message_id']))
        result = {'status': answer['status'], 'stop_reason': answer['stop_reason'],
            'model_calls': [{'purpose': c.purpose, 'model': c.model,
                'input_tokens': c.input_tokens, 'output_tokens': c.output_tokens,
                'usage_source': c.usage_source, 'billed_tokens': c.billed_tokens,
                'status': c.status} for c in calls],
            'usage_tokens_used': usage.tokens_used,
            'assistant_billed_tokens': assistant.billed_tokens,
            'citations': answer['citations']}
    Path(output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    assert calls and all(c.usage_source == 'actual' for c in calls)
    assert assistant.billed_tokens == sum(c.billed_tokens for c in calls)
    assert usage.tokens_used == assistant.billed_tokens


@pytest.mark.asyncio
async def test_embedding_call_is_counted_without_fake_token_charge(pg_session):
    from backend.app.services.research_service import run_persisted_research
    from backend.app.models import EMBEDDING_DIM
    scope, factory = await _setup(pg_session, quota=5000)
    async def embed(text):
        return [0.0] * EMBEDDING_DIM
    result = await run_persisted_research(scope=scope, question='Find?',
        client_request_id='embedding-accounting',
        model=scripted([('search', {'query': 'absent phrase'})], 'Which course do you mean?'),
        model_name='fake', token_alpha=.2,
        token_beta=1, embed_fn=embed, embed_model='BGE-M3',
        session_factory=factory)
    assert result['status'] == 'finished'
    async with factory() as db:
        rows = (await db.scalars(select(ModelCallUsage))).all()
        embedding = next(x for x in rows if x.purpose == 'embedding')
        assert embedding.model == 'BGE-M3' and embedding.usage_source == 'none'
        assert embedding.input_tokens is None and embedding.output_tokens is None
        assert embedding.billed_tokens == 0
        assert len(rows) == 3
