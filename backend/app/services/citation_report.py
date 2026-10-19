"""Which course files, pages and passages the research answers of a lab cite.

For the teacher's audit: per file, then page, then passage, with the students
and their questions; the files no answer cited; the questions that got no
verified answer; the answers a student marked as wrong. Every number is
counted from stored rows (message_citations, agent_runs, messages), never by
a model. Teacher test drives (sessions.is_test) are left out; sessions a
student deleted are kept, as in the rest of the audit.

An "answer" is one assistant message; it may cite several passages of one
page. Counts are distinct answers, students and passages.
"""
from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import (
    AgentRun, Document, DocumentBlock, DocumentRevision, DocumentSource, Lab, Message,
    MessageCitation, MessageFeedback, Session, User,
)

EXAMPLES_PER_PAGE = 5
EXCERPT_CHARS = 160
# Extracted blocks keep some HTML (tables, sub/superscripts), Markdown code
# fences and escapes, and private-use bullet glyphs; an excerpt is plain text.
_INLINE_TAG = re.compile(r'</?(?:sup|sub|b|i|em|strong|span|u)\b[^>]*>', re.IGNORECASE)
_TAG = re.compile(r'<[^>]+>')
_NOISE = re.compile(r'```\w*|[\ue000-\uf8ff]')
_ESCAPE = re.compile(r'\\([\\`*_{}\[\]()#+\-.!|])')


def _excerpt(content: str) -> str:
    text = _TAG.sub(' ', _INLINE_TAG.sub('', content or ''))
    text = ' '.join(_ESCAPE.sub(r'\1', _NOISE.sub(' ', text)).split())
    return text[:EXCERPT_CHARS] + ('…' if len(text) > EXCERPT_CHARS else '')


def _in_period(column, start: datetime | None, end: datetime | None):
    conditions = []
    if start is not None:
        conditions.append(column >= start)
    if end is not None:
        conditions.append(column < end)
    return conditions


async def _lab_files(db: AsyncSession, lab: Lab) -> dict[uuid.UUID, str]:
    """The published PDF files students of the lab can research (as create_scope)."""
    rows = (await db.execute(select(Document.id, Document.filename)
        .join(DocumentRevision, (DocumentRevision.document_id == Document.id) &
                                (DocumentRevision.id == Document.current_revision_id))
        .join(DocumentSource, DocumentSource.id == DocumentRevision.source_version_id)
        .where(Document.class_id == lab.class_id,
               (Document.lab_id == lab.id) | Document.lab_id.is_(None),
               Document.lifecycle == 'active', Document.structured.is_(True),
               DocumentRevision.publication_state == 'published',
               DocumentSource.format == 'pdf'))).all()
    return dict(rows)


async def lab_citation_report(db: AsyncSession, lab: Lab, *, start: datetime | None = None,
                              end: datetime | None = None) -> dict:
    """The report of one lab for [start, end) (either bound may be open)."""
    student_sessions = [Session.lab_id == lab.id, Session.is_test.is_(False)]

    # Each research question of the period, with its student and outcome.
    runs = (await db.execute(select(AgentRun, Session.id, User.username)
        .join(Session, Session.id == AgentRun.session_id)
        .join(User, User.id == AgentRun.user_id)
        .where(*student_sessions, *_in_period(AgentRun.started_at, start, end))
        .order_by(AgentRun.started_at))).all()
    question_of = {run.assistant_message_id: (run, session_id, username)
                   for run, session_id, username in runs if run.assistant_message_id}

    # Each cited passage of an answer of the period.
    cited = (await db.execute(select(MessageCitation, Message.created_at, Message.feedback,
                                     Session.id, Session.user_id, User.username)
        .join(Message, Message.id == MessageCitation.message_id)
        .join(Session, Session.id == Message.session_id)
        .join(User, User.id == Session.user_id)
        .where(*student_sessions, *_in_period(Message.created_at, start, end)))).all()

    files = await _lab_files(db, lab)
    cited_ids = {c.document_id for c, *_ in cited}
    names = dict((await db.execute(select(Document.id, Document.filename)
                                   .where(Document.id.in_(cited_ids)))).all()) if cited_ids else {}

    # One excerpt per cited passage, from the revision the answer cited.
    blocks = {(c.revision_id, c.block_id) for c, *_ in cited if c.block_id}
    excerpts = {}
    if blocks:
        for row in (await db.execute(select(DocumentBlock.revision_id, DocumentBlock.block_id,
                                            DocumentBlock.kind, DocumentBlock.content)
                .where(DocumentBlock.block_id.in_({b for _, b in blocks})))).all():
            if (row.revision_id, row.block_id) in blocks:
                excerpts[(row.revision_id, row.block_id)] = (row.kind, _excerpt(row.content))

    def example(message_id, session_id, user_id, username, created_at):
        run, _, _ = question_of.get(message_id, (None, None, None))
        return {'student': username, 'student_id': str(user_id),
                'question': run.question if run else None,
                'asked_at': created_at.isoformat(), 'session_id': str(session_id),
                'message_id': str(message_id)}

    by_file = defaultdict(lambda: {'answers': set(), 'students': set(), 'pages': defaultdict(
        lambda: {'answers': {}, 'students': set(), 'passages': defaultdict(lambda: {
            'answers': set(), 'revision_id': None})})})
    answers_with_sources, wrong = set(), {}
    for citation, created_at, feedback, session_id, user_id, username in cited:
        message_id = citation.message_id
        answers_with_sources.add(message_id)
        file = by_file[citation.document_id]
        file['answers'].add(message_id)
        file['students'].add(user_id)
        page = file['pages'][citation.pdf_page_no]
        page['answers'][message_id] = example(message_id, session_id, user_id, username, created_at)
        page['students'].add(user_id)
        if citation.block_id:
            passage = page['passages'][citation.block_id]
            passage['answers'].add(message_id)
            passage['revision_id'] = citation.revision_id
        if feedback == MessageFeedback.down:
            entry = wrong.setdefault(message_id, {
                **example(message_id, session_id, user_id, username, created_at), 'pages': set()})
            entry['pages'].add((names.get(citation.document_id) or 'deleted file', citation.pdf_page_no))

    report_files = []
    for document_id, file in by_file.items():
        pages = []
        for page_no, page in file['pages'].items():
            examples = sorted(page['answers'].values(), key=lambda e: e['asked_at'], reverse=True)
            passages = []
            for block_id, passage in page['passages'].items():
                kind, text = excerpts.get((passage['revision_id'], block_id), (None, None))
                passages.append({'block_id': str(block_id), 'kind': kind, 'excerpt': text,
                                 'answers': len(passage['answers'])})
            passages.sort(key=lambda p: -p['answers'])
            pages.append({'pdf_page_no': page_no, 'answers': len(page['answers']),
                          'students': len(page['students']), 'passages': passages,
                          'examples': examples[:EXAMPLES_PER_PAGE]})
        pages.sort(key=lambda p: (-p['answers'], p['pdf_page_no']))
        report_files.append({
            'document_id': str(document_id),
            'filename': names.get(document_id) or 'deleted file',
            'in_lab': document_id in files,
            'answers': len(file['answers']), 'students': len(file['students']),
            'share': round(len(file['answers']) / len(answers_with_sources), 3),
            'pages': pages})
    report_files.sort(key=lambda f: (-f['answers'], f['filename']))

    unverified = [{'student': username, 'student_id': str(run.user_id), 'question': run.question,
                   'asked_at': run.started_at.isoformat(), 'session_id': str(session_id),
                   'outcome': run.stop_reason or run.status}
                  for run, session_id, username in runs
                  if run.status == 'failed' or (run.stop_reason or '').startswith('partial_')]
    return {
        'lab_id': str(lab.id),
        'period': {'start': start.isoformat() if start else None,
                   'end': end.isoformat() if end else None},
        'totals': {'questions': len(runs),
                   'students': len({username for _, _, username in runs}),
                   'answers_with_sources': len(answers_with_sources),
                   'unverified': len(unverified), 'marked_wrong': len(wrong),
                   'questions_back': sum(run.stop_reason == 'clarification' for run, _, _ in runs)},
        'files': report_files,
        'uncited_files': sorted(({'document_id': str(d), 'filename': name}
                                 for d, name in files.items() if d not in by_file),
                                key=lambda f: f['filename']),
        'unverified': unverified[::-1],
        'marked_wrong': sorted(({**w, 'pages': [{'filename': f, 'pdf_page_no': p}
                                                for f, p in sorted(w['pages'])]}
                                for w in wrong.values()),
                               key=lambda w: w['asked_at'], reverse=True),
    }
