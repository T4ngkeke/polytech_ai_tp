"""Compute the vectors of every course document again with the embedding
service configured now (after a change of embedding server or model).

Run it inside the backend container (the API image has no scripts/):

    docker cp scripts/reembed_documents.py edullm-backend:/tmp/
    docker exec -w /app -e PYTHONPATH=/app edullm-backend python /tmp/reembed_documents.py [--apply]

- A PDF document: a published version cannot change, so its published version
  is copied into a new version and indexed again by the worker, as after a
  teacher edit. A version with every page read is published by the worker;
  one with pages not read was published by its teacher anyway, so the copy is
  published the same way here once it is indexed.
- A document from before the PDF path (chunks without a version): its chunk
  vectors are replaced in place, from the same text as when it was indexed
  (context, then content).

Each new vector of an older document must be close to the one it replaces
(cosine >= MIN_COSINE): a lower value means another model or another input,
and nothing is written. Without --apply it only prints what it would do.
"""
import argparse
import asyncio
import math
import time

from sqlalchemy import func, select

from backend.app.database import AsyncSessionLocal
from backend.app.models import Class, DocChunk, Document, DocumentRevision, _utcnow
from backend.app.services import document_versions as versions
from backend.app.services.document_edits import _new_edit_revision
from backend.app.services.model_clients import get_llm_config, make_embed_fn

MIN_COSINE = 0.999
BATCH = 16
WAIT_SECONDS = 1800


def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


async def legacy(db, doc, embed, apply: bool) -> str:
    chunks = (await db.scalars(select(DocChunk).where(DocChunk.document_id == doc.id,
                                                      DocChunk.revision_id.is_(None)))).all()
    lowest = 1.0
    for i in range(0, len(chunks), BATCH):
        part = chunks[i:i + BATCH]
        vectors = await embed([f'{c.context}\n{c.content}' if c.context else c.content for c in part])
        for chunk, vector in zip(part, vectors):
            lowest = min(lowest, cosine(list(chunk.embedding), vector))
            chunk.embedding = vector
    if lowest < MIN_COSINE:
        await db.rollback()
        return f'NOT written: lowest cosine {lowest:.5f} < {MIN_COSINE}'
    if apply:
        await db.commit()
    else:
        await db.rollback()
    return f'{len(chunks)} chunks, lowest cosine {lowest:.5f}'


async def copy_for_index(db, doc, model: str) -> DocumentRevision:
    teacher_id = await db.scalar(select(Class.teacher_id).where(Class.id == doc.class_id))
    _, rev = await _new_edit_revision(db, doc, teacher_id=teacher_id,
        expected_revision_id=doc.current_revision_id, expected_generation=doc.generation,
        note={'action': 'reindex', 'why': f'embedding service changed ({model})', 'at': _utcnow().isoformat()})
    await db.commit()
    return rev


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    async with AsyncSessionLocal() as db:
        config = await get_llm_config(db)
        embed = make_embed_fn(config)
        print('embedding:', config['embedding_model'], config['embedding_base_url'])
        # Plain rows: a rollback below expires the loaded documents.
        rows = (await db.execute(select(Document.id, Document.filename, Document.structured,
                                        Document.current_revision_id)
                                 .where(Document.lifecycle == 'active').order_by(Document.filename))).all()
        copies = {}
        for doc_id, name, structured, current in rows:
            doc = await db.get(Document, doc_id, populate_existing=True)
            if not structured:
                print(f'{name}: legacy, {await legacy(db, doc, embed, args.apply)}')
            elif current is None:
                print(f'{name}: no published version, skipped')
            elif args.apply:
                rev = await copy_for_index(db, doc, config['embedding_model'])
                copies[doc_id] = (name, rev.id)
                print(f'{name}: copy {rev.id} queued for indexing')
            else:
                print(f'{name}: would be copied and indexed again')
        if not copies:
            return
        deadline = time.monotonic() + WAIT_SECONDS
        while time.monotonic() < deadline:
            states = dict((await db.execute(select(DocumentRevision.id, DocumentRevision.index_ready)
                .where(DocumentRevision.id.in_([r for _, r in copies.values()])))).all())
            await db.rollback()
            if all(states.values()):
                break
            print(f'indexed {sum(states.values())}/{len(states)}…')
            await asyncio.sleep(10)
        for doc_id, (name, rev_id) in copies.items():
            doc = await db.get(Document, doc_id, populate_existing=True)
            rev = await db.get(DocumentRevision, rev_id, populate_existing=True)
            if not rev.index_ready:
                print(f'{name}: NOT indexed in time ({rev.processing_state})')
            elif doc.current_revision_id == rev.id:
                print(f'{name}: indexed and published')
            else:
                # Pages not read: the teacher had published the version anyway.
                teacher_id = await db.scalar(select(Class.teacher_id).where(Class.id == doc.class_id))
                await versions.publish_revision(db, document_id=doc.id, revision_id=rev.id, teacher_id=teacher_id,
                                                expected_generation=doc.generation, acknowledge_issues=True)
                await db.commit()
                print(f'{name}: indexed, published as before (pages not read)')
        total = await db.scalar(select(func.count()).select_from(DocChunk).join(
            Document, Document.current_revision_id == DocChunk.revision_id))
        print('chunks of published versions now:', total)


if __name__ == '__main__':
    asyncio.run(main())
