"""M4-C short-transaction research lifecycle and durable source citations.

The student HTTP/SSE wiring is M4-D. This service returns a committed result;
callers must not send `done` before finish_run returns successfully.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from backend.app.agent.result import LABEL_RE, ResearchResult, cited_ids
from backend.app.agent.tools import ResearchScope, _live_session
from backend.app.database import AsyncSessionLocal
from backend.app.models import (
    AgentRun, Document, DocumentAsset, DocumentBlock, DocumentBlockAsset, DocumentPage, Message, MessageCitation,
    ModelCallUsage, SenderType, Session, UsageReservation,
    UsageStat, User, _utcnow,
)
from backend.app.services.document_access import (
    DocumentNotFound, readable_asset, readable_revision,
)
from backend.app.services.page_render import block_box


# A run still active after this long was left by a lost process (restart,
# crash). It is closed so that it does not block its session for good.
STALE_RUN_SECONDS = 600


def _label_order(citation: MessageCitation) -> int:
    # E2 before E10: labels are E<number>.
    return int(citation.label[1:])


def _with_options(message: Message) -> str:
    """An answer as the model sees it in the history: a question back keeps
    the options the student saw, so "the second one" can be understood."""
    options = (message.clarification or {}).get('options') or []
    if not options:
        return message.content
    return message.content + '\nOptions: ' + ' | '.join(options)


async def _close(db, run: AgentRun, status: str, stop_reason: str) -> None:
    active = await db.scalar(select(func.count()).select_from(
        UsageReservation).where(UsageReservation.run_id == run.id,
        UsageReservation.state.in_(('active','unknown')))) or 0
    run.status, run.stop_reason, run.finished_at = status, stop_reason, _utcnow()
    run.settlement_status = 'settled' if active == 0 else 'open'


class RunConflict(ValueError):
    pass


class CitationInvalid(ValueError):
    pass


@dataclass(frozen=True)
class StartOutcome:
    run_id: uuid.UUID
    created: bool
    status: str


class ResearchRuns:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.session_factory = session_factory

    async def begin_run(self, *, scope: ResearchScope, question: str,
                        client_request_id: str,
                        stale_after: float = STALE_RUN_SECONDS) -> StartOutcome:
        if not question.strip() or not 1 <= len(client_request_id) <= 128:
            raise ValueError('invalid_request')
        async with self.session_factory() as db:
            async with db.begin():
                session = await db.scalar(select(Session).where(
                    Session.id == scope.session_id,
                    Session.user_id == scope.user_id,
                    Session.is_deleted.is_(False)).with_for_update())
                if session is None:
                    raise RunConflict('session_unavailable')
                await _live_session(db, scope.user_id, scope.session_id)
                # A user row serializes quota bookkeeping with per-call
                # reservations; the partial unique index handles active runs.
                user = await db.scalar(select(User).where(User.id == scope.user_id)
                                       .with_for_update())
                if user is None or user.is_deleted:
                    raise RunConflict('user_unavailable')
                stale = await db.scalar(select(AgentRun).where(
                    AgentRun.session_id == scope.session_id,
                    AgentRun.status.in_(('running', 'cancelling')),
                    AgentRun.started_at < _utcnow() - timedelta(seconds=stale_after))
                    .with_for_update())
                if stale is not None:
                    await _close(db, stale, 'failed', 'worker_lost')
                    await db.flush()
                values = dict(id=scope.run_id, user_id=scope.user_id,
                    session_id=scope.session_id, client_request_id=client_request_id,
                    question=question, status='running', settlement_status='open',
                    snapshot_map={str(k): str(v) for k, v in scope.snapshot_revisions.items()},
                    started_at=_utcnow())
                dialect_insert = pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert
                inserted = await db.scalar(dialect_insert(AgentRun).values(**values)
                    .on_conflict_do_nothing().returning(AgentRun.id))
                if inserted is None:
                    existing = await db.scalar(select(AgentRun).where(
                        AgentRun.user_id == scope.user_id,
                        AgentRun.client_request_id == client_request_id))
                    if existing is None:
                        raise RunConflict('active_session_run')
                    if existing.session_id != scope.session_id or existing.question != question:
                        raise RunConflict('request_id_payload_conflict')
                    return StartOutcome(existing.id, False, existing.status)
                user_message = Message(session_id=scope.session_id,
                                       sender=SenderType.user, content=question)
                db.add(user_message)
                await db.flush()
                run = await db.get(AgentRun, inserted)
                run.user_message_id = user_message.id
                today = date.today()
                daily = await db.scalar(select(UsageStat).where(
                    UsageStat.user_id == user.id, UsageStat.date == today)
                    .with_for_update())
                if daily is None:
                    db.add(UsageStat(user_id=user.id, date=today,
                                     tokens_used=0, request_count=1))
                else:
                    daily.request_count += 1
                return StartOutcome(run.id, True, run.status)

    async def get_status(self, run_id: uuid.UUID) -> str | None:
        async with self.session_factory() as db:
            return await db.scalar(select(AgentRun.status).where(AgentRun.id == run_id))

    async def request_cancel(self, run_id: uuid.UUID, user_id: uuid.UUID) -> str:
        async with self.session_factory() as db:
            async with db.begin():
                run = await db.scalar(select(AgentRun).where(AgentRun.id == run_id,
                    AgentRun.user_id == user_id).with_for_update())
                if run is None:
                    raise RunConflict('run_not_found')
                if run.status == 'running':
                    run.status = 'cancelling'
                return run.status

    async def mark_terminal(self, run_id: uuid.UUID, *, status: str,
                            stop_reason: str) -> str:
        if status not in ('failed', 'cancelled'):
            raise ValueError('invalid_terminal_status')
        async with self.session_factory() as db:
            async with db.begin():
                run = await db.scalar(select(AgentRun).where(
                    AgentRun.id == run_id).with_for_update())
                if run is None:
                    raise RunConflict('run_not_found')
                if run.status in ('finished', 'failed', 'cancelled'):
                    return run.status
                await _close(db, run, status, stop_reason)
                return run.status

    async def _validate_citation(self, db, run: AgentRun, item: dict) -> dict:
        try:
            doc_id = uuid.UUID(str(item['document_id']))
            rev_id = uuid.UUID(str(item['revision_id']))
            page_id = uuid.UUID(str(item['page_id']))
            block_id = uuid.UUID(str(item['block_id'])) if item.get('block_id') else None
            asset_id = uuid.UUID(str(item['asset_id'])) if item.get('asset_id') else None
            page_no = item['pdf_page_no']
            label = item['evidence_id']
            origin = item['origin']
            span = item.get('range')
            if (not isinstance(page_no, int) or page_no < 1 or
                    not LABEL_RE.fullmatch(label) or
                    not isinstance(origin, str) or len(origin) > 32):
                raise ValueError
            if run.snapshot_map.get(str(doc_id)) != str(rev_id):
                raise ValueError
            await readable_revision(db, document_id=doc_id, revision_id=rev_id,
                                    user_id=run.user_id, session_id=run.session_id)
            page = await db.get(DocumentPage, (rev_id, page_id))
            if page is None or page.preview_page_no != page_no:
                raise ValueError
            visual = origin == 'live_visual'
            if visual and asset_id is None:
                raise ValueError
            if block_id is not None:
                block = await db.get(DocumentBlock, (rev_id, block_id))
                if block is None or block.page_id != page_id:
                    raise ValueError
                if visual:
                    if span is not None:
                        raise ValueError
                    start = end = None
                else:
                    if not isinstance(span, dict):
                        raise ValueError
                    start, end = span['start'], span['end']
                    if (not isinstance(start, int) or not isinstance(end, int)
                            or not 0 <= start < end <= len(block.content or '')):
                        raise ValueError
                    actual_origin = (block.source_ref or {}).get('content_origin', 'native')
                    if origin != actual_origin:
                        raise ValueError
            elif asset_id is None or span is not None or not visual:
                raise ValueError
            else:
                start = end = None
            if asset_id is not None:
                await readable_asset(db, asset_id=asset_id, document_id=doc_id,
                    revision_id=rev_id, user_id=run.user_id, session_id=run.session_id)
                if visual and asset_id != page.preview_asset_id:
                    if block_id is None or not await db.scalar(select(DocumentBlockAsset)
                        .where(DocumentBlockAsset.revision_id == rev_id,
                               DocumentBlockAsset.block_id == block_id,
                               DocumentBlockAsset.asset_id == asset_id)):
                        raise ValueError
            return dict(label=label, document_id=doc_id, revision_id=rev_id,
                page_id=page_id, pdf_page_no=page_no, block_id=block_id,
                range_start=start, range_end=end, asset_id=asset_id, origin=origin)
        except (KeyError, TypeError, ValueError, DocumentNotFound) as exc:
            raise CitationInvalid('citation_identity_or_access_invalid') from exc

    async def finish_run(self, run_id: uuid.UUID, result: ResearchResult) -> uuid.UUID:
        """Commit answer, used citations, focus and terminal state atomically."""
        if not result.answer.strip():
            raise ValueError('empty_answer')
        labels = [x['evidence_id'] for x in result.citations]
        if (len(labels) != len(set(labels)) or
                cited_ids(result.answer) != set(labels)):
            raise CitationInvalid('answer_citation_mismatch')
        async with self.session_factory() as db:
            async with db.begin():
                run = await db.scalar(select(AgentRun).where(
                    AgentRun.id == run_id).with_for_update())
                if run is None:
                    raise RunConflict('run_not_found')
                if run.status == 'finished':
                    return run.assistant_message_id
                if run.status != 'running':
                    raise RunConflict('run_not_running')
                active = await db.scalar(select(func.count()).select_from(
                    UsageReservation).where(UsageReservation.run_id == run.id,
                    UsageReservation.state.in_(('active','unknown')))) or 0
                if active:
                    raise RunConflict('calls_not_settled')
                validated = [await self._validate_citation(db, run, c)
                             for c in result.citations]
                await _live_session(db, run.user_id, run.session_id)
                calls = (await db.scalars(select(ModelCallUsage).where(
                    ModelCallUsage.run_id == run.id))).all()
                prompt_tokens = sum(x.input_tokens or 0 for x in calls)
                completion_tokens = sum(x.output_tokens or 0 for x in calls)
                billed_tokens = sum(x.billed_tokens for x in calls)
                assistant = Message(session_id=run.session_id, sender=SenderType.llm,
                    content=result.answer, prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                    billed_tokens=billed_tokens, clarification=result.clarification,
                    answer_mode=result.mode)
                db.add(assistant)
                await db.flush()
                citations = [MessageCitation(message_id=assistant.id,
                                             availability='available', **v)
                             for v in validated]
                db.add_all(citations)
                await db.flush()
                run.assistant_message_id = assistant.id
                run.status, run.stop_reason = 'finished', result.stop_reason
                run.settlement_status, run.finished_at = 'settled', _utcnow()
                return assistant.id

    async def load_result(self, *, run_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        async with self.session_factory() as db:
            run = await db.scalar(select(AgentRun).where(
                AgentRun.id == run_id, AgentRun.user_id == user_id))
            if run is None:
                raise RunConflict('run_not_found')
            # Ownership suffices to show old chat text. Current course access is
            # checked separately for each citation and can make links unavailable.
            owner = await db.scalar(select(Session.id).where(
                Session.id == run.session_id, Session.user_id == user_id,
                Session.is_deleted.is_(False)))
            if owner is None:
                raise RunConflict('session_unavailable')
            answer = await db.get(Message, run.assistant_message_id) if run.assistant_message_id else None
            rows = [] if answer is None else sorted((await db.scalars(select(MessageCitation).where(
                MessageCitation.message_id == answer.id))).all(), key=_label_order)
            citations = await self._citation_payloads(db, [(run, c) for c in rows])
            return {'run_id': str(run.id), 'status': run.status,
                    'stop_reason': run.stop_reason, 'message_id': str(answer.id) if answer else None,
                    'answer': answer.content if answer else None, 'citations': citations,
                    'clarification': answer.clarification if answer else None,
                    'mode': answer.answer_mode if answer else None}

    async def load_session_citations(self, *, session_id: uuid.UUID,
                                     user_id: uuid.UUID) -> dict[uuid.UUID, list[dict]]:
        """Restore citations on chat refresh, checking current access."""
        async with self.session_factory() as db:
            owner = await db.scalar(select(Session.id).where(
                Session.id == session_id, Session.user_id == user_id,
                Session.is_deleted.is_(False)))
            if owner is None:
                raise DocumentNotFound('not_found')
            runs = {run.assistant_message_id: run for run in (await db.scalars(select(AgentRun).where(
                AgentRun.session_id == session_id, AgentRun.user_id == user_id,
                AgentRun.assistant_message_id.is_not(None)))).all()}
            rows = sorted((await db.scalars(select(MessageCitation).where(
                MessageCitation.message_id.in_(runs.keys())))).all(), key=_label_order) if runs else []
            payloads = await self._citation_payloads(db, [(runs[c.message_id], c) for c in rows])
            result = {message_id: [] for message_id in runs}
            for c, payload in zip(rows, payloads):
                result[c.message_id].append(payload)
            return result

    async def _citation_payloads(self, db, items: list[tuple[AgentRun, MessageCitation]]) -> list[dict]:
        """Current availability and links of stored citations, in item order.

        Access is checked once per source (revision, asset), and pages, blocks,
        documents and assets are loaded in batches: a session reload costs the
        same few queries for 3 citations or 30.
        """
        if not items:
            return []
        revisions, assets_ok, links = {}, {}, {}

        async def revision(run, c):
            key = (c.document_id, c.revision_id, run.user_id, run.session_id)
            if key not in revisions:
                try:
                    revisions[key] = await readable_revision(db, document_id=c.document_id,
                        revision_id=c.revision_id, user_id=run.user_id, session_id=run.session_id)
                except DocumentNotFound:
                    revisions[key] = None
            return revisions[key]

        async def asset_readable(run, c):
            key = (c.asset_id, c.document_id, c.revision_id, run.user_id, run.session_id)
            if key not in assets_ok:
                try:
                    await readable_asset(db, asset_id=c.asset_id, document_id=c.document_id,
                        revision_id=c.revision_id, user_id=run.user_id, session_id=run.session_id)
                    assets_ok[key] = True
                except DocumentNotFound:
                    assets_ok[key] = False
            return assets_ok[key]

        cs = [c for _, c in items]
        pages = {(p.revision_id, p.page_id): p for p in (await db.scalars(select(DocumentPage).where(
            tuple_(DocumentPage.revision_id, DocumentPage.page_id).in_(
                {(c.revision_id, c.page_id) for c in cs})))).all()}
        block_keys = {(c.revision_id, c.block_id) for c in cs if c.block_id is not None}
        blocks = {(b.revision_id, b.block_id): b for b in (await db.scalars(select(DocumentBlock).where(
            tuple_(DocumentBlock.revision_id, DocumentBlock.block_id).in_(block_keys)))).all()
        } if block_keys else {}
        docs = {d.id: d for d in (await db.scalars(select(Document).where(
            Document.id.in_({c.document_id for c in cs})))).all()}
        asset_ids = {c.asset_id for c in cs if c.asset_id is not None}
        assets = {a.id: a for a in (await db.scalars(select(DocumentAsset).where(
            DocumentAsset.id.in_(asset_ids)))).all()} if asset_ids else {}

        payloads = []
        for run, c in items:
            available = c.availability == 'available'
            teacher_revised = False
            if available:
                rev = await revision(run, c)
                if rev is None:
                    available = False
                else:
                    teacher_revised = rev.reason == 'teacher_edit'
                    page = pages.get((c.revision_id, c.page_id))
                    if page is None or page.preview_page_no != c.pdf_page_no:
                        available = False
                    if c.block_id is not None:
                        block = blocks.get((c.revision_id, c.block_id))
                        if block is None or block.page_id != c.page_id:
                            available = False
                        elif block.edited_by_teacher:
                            teacher_revised = True
                        elif c.origin != 'live_visual' and (
                                c.range_start is None or c.range_end is None or
                                c.range_end > len(block.content or '')):
                            available = False
                    if c.asset_id is not None:
                        if not await asset_readable(run, c):
                            available = False
                        elif (c.origin == 'live_visual' and page is not None and
                                c.asset_id != page.preview_asset_id):
                            key = (c.revision_id, c.block_id, c.asset_id)
                            if key not in links:
                                links[key] = await db.scalar(select(DocumentBlockAsset).where(
                                    DocumentBlockAsset.revision_id == c.revision_id,
                                    DocumentBlockAsset.block_id == c.block_id,
                                    DocumentBlockAsset.asset_id == c.asset_id)) is not None
                            if not links[key]:
                                available = False
            doc = docs.get(c.document_id)
            asset = assets.get(c.asset_id) if c.asset_id and available else None
            newer_revision = bool(doc and doc.current_revision_id != c.revision_id)
            href = (f'/source/{c.document_id}?session_id={run.session_id}'
                    f'&revision_id={c.revision_id}&page={c.pdf_page_no}') if available else None
            block = blocks.get((c.revision_id, c.block_id)) if c.block_id is not None else None
            payloads.append({'citation_id': str(c.id), 'label': c.label,
                'document_id': str(c.document_id), 'revision_id': str(c.revision_id),
                'page_id': str(c.page_id), 'pdf_page_no': c.pdf_page_no,
                'block_id': str(c.block_id) if c.block_id else None,
                'range': {'start': c.range_start, 'end': c.range_end}
                if c.range_start is not None else None,
                'origin': c.origin, 'available': available, 'href': href,
                'filename': doc.filename if doc else None,
                'newer_revision': newer_revision,
                'teacher_revised': teacher_revised,
                'asset_id': str(c.asset_id) if asset else None,
                'asset_mime': asset.mime if asset else None,
                # Where the cited block sits on its page (fractions), for the
                # preview's highlight.
                'box': block_box(block.source_ref) if available and block is not None else None})
        return payloads

    async def last_answer_asked(self, session_id: uuid.UUID) -> bool:
        """Whether the session's latest answer is a question back: the next run
        must answer, so the chat does not offer ask_student twice in a row."""
        async with self.session_factory() as db:
            latest = await db.scalar(select(Message).where(
                Message.session_id == session_id, Message.sender == SenderType.llm)
                .order_by(Message.created_at.desc()).limit(1))
            return bool(latest is not None and latest.clarification)

    async def next_evidence_number(self, session_id: uuid.UUID) -> int:
        """The first evidence number for a new run of the session: after every
        ID its earlier answers cite, so an old ID never names a new source."""
        async with self.session_factory() as db:
            labels = (await db.scalars(select(MessageCitation.label)
                .join(AgentRun, AgentRun.assistant_message_id == MessageCitation.message_id)
                .where(AgentRun.session_id == session_id))).all()
        return 1 + max((int(label[1:]) for label in labels), default=0)

    async def load_safe_history(self, scope: ResearchScope, *, limit: int = 20) -> list[dict]:
        return (await self.load_safe_context(scope, limit=limit))[0]

    async def load_safe_context(self, scope: ResearchScope, *,
                                limit: int = 20) -> tuple[list[dict], dict[str, dict]]:
        """The safe chat history, and the citations of the answers in it by label.

        A session numbers evidence across its runs, so a label in an earlier
        answer names one stored source; a new answer may cite it again, and
        finish_run validates it like a new one. A label used for two different
        sources (sessions from before the numbering) is ambiguous and left out.
        """
        async with self.session_factory() as db:
            await _live_session(db, scope.user_id, scope.session_id)
            messages = list((await db.scalars(select(Message).where(
                Message.session_id == scope.session_id).order_by(
                Message.created_at.desc()).limit(limit))).all())
            messages.reverse()
            answer_ids = [m.id for m in messages if m.sender != SenderType.user]
            runs = {run.assistant_message_id: run for run in (await db.scalars(select(AgentRun).where(
                AgentRun.assistant_message_id.in_(answer_ids),
                AgentRun.user_id == scope.user_id))).all()} if answer_ids else {}
            citations = (await db.scalars(select(MessageCitation).where(
                MessageCitation.message_id.in_(runs.keys())))).all() if runs else []
            # Only citations of the current snapshot need the access check.
            current = [c for c in citations
                       if scope.snapshot_revisions.get(c.document_id) == c.revision_id]
            payloads = await self._citation_payloads(db, [(runs[c.message_id], c) for c in current])
            unavailable = {c.message_id for c in citations
                           if scope.snapshot_revisions.get(c.document_id) != c.revision_id}
            unavailable |= {c.message_id for c, p in zip(current, payloads) if not p['available']}
            # An answer without citations may still repeat pages it read
            # (nothing forces the model to cite). It is kept only while every
            # document its run could read is still readable at the same
            # revision; a greeting or a clarification usually is.
            snapshot = {str(k): str(v) for k, v in scope.snapshot_revisions.items()}
            cited = {c.message_id for c in citations}
            # A question back (ask_student) states no course content: it stays.
            unavailable |= {message_id for message_id, run in runs.items() if message_id not in cited
                            and run.stop_reason != 'clarification'
                            and any(snapshot.get(d) != r for d, r in run.snapshot_map.items())}
            history = []
            for message in messages:
                if message.sender == SenderType.user:
                    # A question whose answer is missing (failed, cancelled) or
                    # left out is dropped with it: turns must alternate.
                    if history and history[-1]['role'] == 'user':
                        history.pop()
                    history.append({'role': 'user', 'content': message.content})
                # Legacy assistant text (no research run) may depend on revoked
                # material. Do not put it into a new research prompt. A research
                # answer with citations is kept only while every cited source
                # is still available.
                elif message.id in runs and message.id not in unavailable:
                    if history and history[-1]['role'] == 'user':
                        history.append({'role': 'assistant', 'content': _with_options(message)})
                elif history and history[-1]['role'] == 'user':
                    history.pop()
            # The current question follows; an earlier one without an answer goes.
            if history and history[-1]['role'] == 'user':
                history.pop()
            sources = {}
            for c in current:
                if c.message_id in unavailable:
                    continue
                sources.setdefault(c.label, []).append({
                    'evidence_id': c.label, 'document_id': str(c.document_id),
                    'revision_id': str(c.revision_id), 'page_id': str(c.page_id),
                    'pdf_page_no': c.pdf_page_no,
                    'block_id': str(c.block_id) if c.block_id else None,
                    'range': {'start': c.range_start, 'end': c.range_end}
                    if c.range_start is not None else None,
                    'asset_id': str(c.asset_id) if c.asset_id else None,
                    'origin': c.origin})
            # A label re-cited for the same source stays; one used for two
            # different sources is ambiguous.
            earlier = {label: found[0] for label, found in sources.items()
                       if all(x == found[0] for x in found)}
            return history, earlier
