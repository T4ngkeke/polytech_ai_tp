"""ORM defense for immutable snapshots (PostgreSQL migration also installs triggers).

Bulk SQL is reserved for lifecycle services; it must obey the same DB triggers.
"""
from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session
from backend.app.models import (Document, DocumentSource, DocumentRevision, DocumentAsset,
    DocumentPage, DocumentBlock, DocumentBlockAsset, DocumentBlockLink, DocChunk)


@event.listens_for(Session, 'before_flush')
def protect_snapshots(session, flush_context, instances):
    content_types = (DocumentPage, DocumentBlock, DocumentBlockAsset, DocumentBlockLink, DocChunk)
    for obj in session.new | session.dirty | session.deleted:
        if obj in session.dirty and not session.is_modified(obj, include_collections=False):
            continue
        if isinstance(obj, (DocumentSource, DocumentAsset)) and obj in session.dirty:
            raise ValueError('immutable_source_or_asset')
        if isinstance(obj, content_types):
            if obj in session.dirty and inspect(obj).attrs.revision_id.history.has_changes():
                raise ValueError("immutable_revision_identity")
            rev_id = obj.revision_id
            if rev_id is None:  # legacy chunks
                continue
            row = session.execute(select(DocumentRevision.publication_state, Document.lifecycle)
                .join(Document, Document.id == DocumentRevision.document_id)
                .where(DocumentRevision.id == rev_id)).first()
            if row and (row[0] != 'draft' or row[1] != 'active'):
                if obj in session.deleted and row[1] == 'deleted':
                    continue
                raise ValueError('immutable_revision')
        if isinstance(obj, DocumentRevision) and obj in session.dirty:
            stored = session.execute(select(DocumentRevision.publication_state).where(
                DocumentRevision.id == obj.id)).scalar_one_or_none()
            if stored and stored != 'draft':
                changed = {attr.key for attr in inspect(obj).attrs if attr.history.has_changes()}
                if changed - {'publication_state'} or obj.publication_state != 'superseded':
                    raise ValueError('immutable_revision')
