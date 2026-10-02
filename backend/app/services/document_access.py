"""M1 authorization: derive scope from persisted identity, never model arguments."""
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models import (
    Class, ClassStudent, Document, DocumentAsset, DocumentBlockAsset,
    DocumentPage, DocumentRevision, Lab, Session, User, UserRole,
)


class DocumentNotFound(ValueError):
    """Deliberately indistinguishable absent / unauthorized resource."""


async def owned_document(db: AsyncSession, document_id: uuid.UUID, teacher_id: uuid.UUID):
    doc = await db.scalar(select(Document).join(Class, Document.class_id == Class.id)
        .join(User, User.id == Class.teacher_id).where(
            Document.id == document_id, Class.teacher_id == teacher_id,
            Class.is_deleted.is_(False), User.is_deleted.is_(False),
            User.role.in_([UserRole.teacher, UserRole.admin]))
        .execution_options(populate_existing=True))
    if doc is None:
        raise DocumentNotFound("not_found")
    return doc


async def readable_document(db, *, document_id, user_id, session_id=None, teacher=False):
    """Resolve a document only inside the caller's current class/lab scope."""
    if teacher:
        doc = await owned_document(db, document_id, user_id)
    else:
        scope = (await db.execute(select(Session, Lab, Class).join(Lab, Session.lab_id == Lab.id)
            .join(Class, Lab.class_id == Class.id).join(User, Session.user_id == User.id)
            .where(Session.id == session_id, Session.user_id == user_id,
                Session.is_deleted.is_(False), Lab.is_deleted.is_(False),
                Lab.is_active.is_(True), Class.is_deleted.is_(False), User.is_deleted.is_(False)))).first()
        if scope is None:
            raise DocumentNotFound("not_found")
        chat, lab, cls = scope
        membership = await db.get(ClassStudent, (cls.id, user_id))
        if membership is None and not (chat.is_test and cls.teacher_id == user_id):
            raise DocumentNotFound("not_found")
        doc = await db.scalar(select(Document).where(Document.id == document_id,
            Document.class_id == cls.id,
            (Document.lab_id == lab.id) | Document.lab_id.is_(None))
            .execution_options(populate_existing=True))
        if doc is None or doc.lifecycle != "active":
            raise DocumentNotFound("not_found")
    if doc.lifecycle == "deleted":
        raise DocumentNotFound("not_found")
    return doc


async def readable_revision(db, *, document_id, user_id, revision_id=None,
                            session_id=None, teacher=False):
    """Explicit historical IDs remain readable until document/scope revocation."""
    doc = await readable_document(db, document_id=document_id, user_id=user_id,
                                  session_id=session_id, teacher=teacher)
    rev = await db.scalar(select(DocumentRevision).where(
        DocumentRevision.id == (revision_id or doc.current_revision_id),
        DocumentRevision.document_id == doc.id).execution_options(populate_existing=True))
    if rev is None or (not teacher and rev.publication_state not in ("published", "superseded")):
        raise DocumentNotFound("not_found")
    return rev


async def readable_asset(db, *, asset_id, **scope):
    rev = await readable_revision(db, **scope)
    asset = await db.get(DocumentAsset, asset_id)
    if asset is None or asset.source_version_id != rev.source_version_id:
        raise DocumentNotFound("not_found")
    if not scope.get("teacher", False) and asset.access_class != "student_visible":
        raise DocumentNotFound("not_found")
    linked = asset_id == rev.preview_pdf_asset_id
    linked = linked or await db.scalar(select(DocumentPage.page_id).where(
        DocumentPage.revision_id == rev.id, DocumentPage.preview_asset_id == asset_id).limit(1))
    linked = linked or await db.scalar(select(DocumentBlockAsset.block_id).where(
        DocumentBlockAsset.revision_id == rev.id, DocumentBlockAsset.asset_id == asset_id).limit(1))
    if not linked:
        raise DocumentNotFound("not_found")
    return asset
