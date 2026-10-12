"""M4-C short-transaction research lifecycle and durable source citations.

The student HTTP/SSE wiring is M4-D. This service returns a committed result;
callers must not send `done` before finish_run returns successfully.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
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


# A run still active after this long was left by a lost process (restart,
# crash). It is closed so that it does not block its session for good.
STALE_RUN_SECONDS = 600


def _label_order(citation: MessageCitation) -> int:
    # E2 before E10: labels are E<number>.
    return int(citation.label[1:])


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
                    billed_tokens=billed_tokens)
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
            citations = [await self._citation_payload(db, run, c) for c in rows]
            return {'run_id': str(run.id), 'status': run.status,
                    'stop_reason': run.stop_reason, 'message_id': str(answer.id) if answer else None,
                    'answer': answer.content if answer else None, 'citations': citations}

    async def load_session_citations(self, *, session_id: uuid.UUID,
                                     user_id: uuid.UUID) -> dict[uuid.UUID, list[dict]]:
        """Restore citations on chat refresh, checking current access."""
        async with self.session_factory() as db:
            owner = await db.scalar(select(Session.id).where(
                Session.id == session_id, Session.user_id == user_id,
                Session.is_deleted.is_(False)))
            if owner is None:
                raise DocumentNotFound('not_found')
            runs = (await db.scalars(select(AgentRun).where(
                AgentRun.session_id == session_id, AgentRun.user_id == user_id,
                AgentRun.assistant_message_id.is_not(None)))).all()
            result = {}
            for run in runs:
                rows = sorted((await db.scalars(select(MessageCitation).where(
                    MessageCitation.message_id == run.assistant_message_id))).all(),
                    key=_label_order)
                result[run.assistant_message_id] = [
                    await self._citation_payload(db, run, c) for c in rows]
            return result

    async def _citation_payload(self, db, run: AgentRun, c: MessageCitation) -> dict:
        available = c.availability == 'available'
        teacher_revised = False
        if available:
            try:
                revision = await readable_revision(db, document_id=c.document_id,
                    revision_id=c.revision_id, user_id=run.user_id,
                    session_id=run.session_id)
                teacher_revised = revision.reason == 'teacher_edit'
                page = await db.get(DocumentPage, (c.revision_id, c.page_id))
                if page is None or page.preview_page_no != c.pdf_page_no:
                    available = False
                if c.block_id is not None:
                    block = await db.get(DocumentBlock, (c.revision_id, c.block_id))
                    if block is None or block.page_id != c.page_id:
                        available = False
                    elif block.edited_by_teacher:
                        teacher_revised = True
                    elif c.origin != 'live_visual' and (
                            c.range_start is None or c.range_end is None or
                            c.range_end > len(block.content or '')):
                        available = False
                if c.asset_id is not None:
                    await readable_asset(db, asset_id=c.asset_id,
                        document_id=c.document_id, revision_id=c.revision_id,
                        user_id=run.user_id, session_id=run.session_id)
                    if (c.origin == 'live_visual' and page is not None and
                            c.asset_id != page.preview_asset_id):
                        linked = await db.scalar(select(DocumentBlockAsset).where(
                            DocumentBlockAsset.revision_id == c.revision_id,
                            DocumentBlockAsset.block_id == c.block_id,
                            DocumentBlockAsset.asset_id == c.asset_id))
                        if linked is None:
                            available = False
            except DocumentNotFound:
                available = False
        doc = await db.get(Document, c.document_id)
        asset = await db.get(DocumentAsset, c.asset_id) if c.asset_id and available else None
        newer_revision = bool(doc and doc.current_revision_id != c.revision_id)
        href = (f'/source/{c.document_id}?session_id={run.session_id}'
                f'&revision_id={c.revision_id}&page={c.pdf_page_no}') if available else None
        return {'citation_id': str(c.id), 'label': c.label,
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
                'asset_mime': asset.mime if asset else None}

    async def next_evidence_number(self, session_id: uuid.UUID) -> int:
        """The first evidence number for a new run of the session: after every
        ID its earlier answers cite, so a copied old ID is never valid again."""
        async with self.session_factory() as db:
            labels = (await db.scalars(select(MessageCitation.label)
                .join(AgentRun, AgentRun.assistant_message_id == MessageCitation.message_id)
                .where(AgentRun.session_id == session_id))).all()
        return 1 + max((int(label[1:]) for label in labels), default=0)

    async def load_safe_history(self, scope: ResearchScope, *, limit: int = 20) -> list[dict]:
        async with self.session_factory() as db:
            await _live_session(db, scope.user_id, scope.session_id)
            messages = list((await db.scalars(select(Message).where(
                Message.session_id == scope.session_id).order_by(
                Message.created_at.desc()).limit(limit))).all())
            messages.reverse()
            history = []
            for message in messages:
                if message.sender == SenderType.user:
                    history.append({'role': 'user', 'content': message.content})
                    continue
                # Legacy assistant text (no research run) may depend on revoked
                # material. Do not put it into a new research prompt.
                run = await db.scalar(select(AgentRun).where(
                    AgentRun.assistant_message_id == message.id,
                    AgentRun.user_id == scope.user_id))
                if run is None:
                    continue
                # A research answer without citations (a greeting, a general
                # answer) used no course material; one with citations is kept
                # only while every cited source is still available.
                citations = (await db.scalars(select(MessageCitation).where(
                    MessageCitation.message_id == message.id))).all()
                available = True
                for citation in citations:
                    if (scope.snapshot_revisions.get(citation.document_id) != citation.revision_id
                            or not (await self._citation_payload(db, run, citation))['available']):
                        available = False
                        break
                if available:
                    history.append({'role': 'assistant', 'content': message.content})
            return history
