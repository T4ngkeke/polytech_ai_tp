"""Run-local, immutable evidence from verified reader results.

Search/open/grep return leads, never citeable evidence. A read range remains
partial even if it happens to contain the answer; source completeness is a
separate ingestion property. Evidence is intentionally not persisted here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID


@dataclass(frozen=True)
class Evidence:
    id: str
    document_id: UUID
    revision_id: UUID
    page_id: UUID
    pdf_page_no: int
    block_id: UUID | None
    start: int
    end: int
    block_length: int
    content: str
    kind: str
    source_completeness: str
    issues: tuple
    origin: str
    asset_ids: tuple[str, ...]
    asset_id: str | None = None

    @property
    def returned_complete_block(self) -> bool:
        return self.start == 0 and self.end == self.block_length

    def citation(self) -> dict:
        return {
            'evidence_id': self.id, 'document_id': str(self.document_id),
            'revision_id': str(self.revision_id), 'page_id': str(self.page_id),
            'pdf_page_no': self.pdf_page_no,
            'block_id': str(self.block_id) if self.block_id else None,
            'range': None if self.origin == 'live_visual' else {'start': self.start, 'end': self.end},
            'asset_id': self.asset_id, 'kind': self.kind,
            'source_completeness': self.source_completeness,
            'returned_complete_block': self.returned_complete_block,
            'issues': list(self.issues), 'origin': self.origin,
            'asset_ids': list(self.asset_ids),
        }


class EvidenceStore:
    def __init__(self, snapshot_revisions: Mapping[UUID, UUID], *, first_number: int = 1):
        # first_number: a chat session numbers evidence across its runs, so an
        # ID copied from an earlier answer is never valid in a later run.
        self.snapshot_revisions = snapshot_revisions
        self.first_number = first_number
        self._items: dict[str, Evidence] = {}
        self._keys: dict[tuple, str] = {}

    def __len__(self) -> int:
        return len(self._items)

    def get(self, evidence_id: str) -> Evidence | None:
        return self._items.get(evidence_id)

    def all(self) -> list[Evidence]:
        return list(self._items.values())

    def touch(self, evidence_id: str) -> None:
        """A repeated read moves its evidence to the end of all() (most recent last)."""
        self._items[evidence_id] = self._items.pop(evidence_id)

    def add_read(self, document_id: UUID, result: dict) -> list[str]:
        """Register only successful, internally consistent read ranges."""
        if not result.get('ok') or document_id not in self.snapshot_revisions:
            return []
        revision_id = self.snapshot_revisions[document_id]
        added: list[str] = []
        for item in result.get('items', []):
            try:
                page = item['page']
                content = item['content']
                start, end = item['range']['start'], item['range']['end']
                length = item['block_length']
                if (not isinstance(content, str) or not content or not isinstance(start, int)
                        or not isinstance(end, int) or not isinstance(length, int)
                        or not 0 <= start <= end <= length or len(content) != end - start
                        or not isinstance(page['preview_page_no'], int)
                        or page['preview_page_no'] < 1):
                    continue
                page_id = UUID(page['page_id'])
                block_id = UUID(item['block_id'])
            except (KeyError, TypeError, ValueError):
                continue
            source = item.get('source') or {}
            origin = str(source.get('content_origin') or 'unknown')
            key = (document_id, revision_id, page_id, block_id, start, end,
                   origin, content)
            evidence_id = self._keys.get(key)
            if evidence_id is None:
                evidence_id = f'E{self.first_number + len(self._items)}'
                evidence = Evidence(
                    evidence_id, document_id, revision_id, page_id,
                    page['preview_page_no'], block_id, start, end, length, content,
                    str(item.get('type') or 'unknown'),
                    str(item.get('source_completeness') or 'unknown'),
                    tuple(item.get('issues') or ()), origin,
                    tuple(str(a['asset_id']) for a in item.get('assets', [])
                          if isinstance(a, dict) and a.get('asset_id')),
                )
                self._items[evidence_id] = evidence
                self._keys[key] = evidence_id
                added.append(evidence_id)
            else:
                self.touch(evidence_id)
        return added

    def add_visual(self, result: dict) -> list[str]:
        """Cite a successful visual observation as visual, never as PDF text."""
        if not result.get('ok'):
            return []
        added = []
        for item in result.get('items', []):
            try:
                doc_id, rev_id = UUID(item['document_id']), UUID(item['revision_id'])
                page_id, asset_id = UUID(item['page_id']), UUID(item['asset_id'])
                block_id = UUID(item['block_id']) if item.get('block_id') else None
                content, page_no = item['content'], item['pdf_page_no']
                if (self.snapshot_revisions.get(doc_id) != rev_id or
                        not isinstance(content, str) or not content.strip() or
                        not isinstance(page_no, int) or page_no < 1 or
                        item.get('origin') != 'live_visual'):
                    continue
            except (KeyError, ValueError, TypeError):
                continue
            key = (doc_id, rev_id, page_id, asset_id, content)
            evidence_id = self._keys.get(key)
            if evidence_id is None:
                evidence_id = f'E{self.first_number + len(self._items)}'
                evidence = Evidence(evidence_id, doc_id, rev_id, page_id,
                    page_no, block_id, 0, 0, 0, content, 'figure', 'visual_observation',
                    tuple(item.get('uncertainties') or ()), 'live_visual',
                    (str(asset_id),), str(asset_id))
                self._items[evidence_id] = evidence
                self._keys[key] = evidence_id
                added.append(evidence_id)
            else:
                self.touch(evidence_id)
        return added
