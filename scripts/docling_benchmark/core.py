"""Shared output and review contract. Semantic grading requires an identified reviewer."""
import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field


@dataclass
class Segment:
    content: str
    source_text: str
    source_ref: dict
    extracted_content: str = ''
    kind: str = 'native'


@dataclass
class PageResult:
    file_sha256: str
    page_no: int
    implementation: str
    segments: list[Segment] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    model: str = 'ornith'
    seconds: float = 0
    calls: int = 0
    usage: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def searchable_text(self):
        return '\n\n'.join(s.content for s in self.segments if s.content.strip())

    @property
    def status(self):
        if self.reviews or self.errors:
            return 'partial' if self.searchable_text.strip() else 'needs_review'
        return 'automatic' if self.searchable_text.strip() else 'empty'

    def dump(self):
        return dict(asdict(self), status=self.status, searchable_text=self.searchable_text)


def output_hash(page):
    value=page.dump() if isinstance(page, PageResult) else page
    # Timing is not content. Sources and pending candidates DO affect review identity.
    value={k:v for k,v in value.items() if k not in ('seconds','calls','usage','metadata')}
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def assess(page, *, required=(), forbidden=()):
    """Literal checks only; never a substitute for semantic/visual review."""
    text=page.searchable_text
    present=all(x in text for x in required)
    contradiction=any(x in text for x in forbidden)
    return 'mixed' if present and contradiction else 'wrong' if contradiction else 'correct' if present else 'missing'


def bind_review(page, checks, *, reviewer, kind, added_errors=()):
    if kind not in ('model','human') or not reviewer.strip():
        raise ValueError('Reviewer identity and kind required')
    return dict(output_sha256=output_hash(page), reviewer=reviewer, reviewer_kind=kind,
                checks=checks, added_errors=list(added_errors))


def validate_review(page, review):
    if output_hash(page)!=review.get('output_sha256'):
        raise ValueError('Output changed: review is stale')
    return True


def add_review(page, source, reason, code='content_review', candidate=None):
    page.reviews.append(dict(source_ref=source, page_no=page.page_no,
                             file_sha256=page.file_sha256, reason=reason,
                             reason_code=code, candidate_content=candidate))


async def picture_result(page, source, request):
    page.calls+=1
    try:
        value=await asyncio.wait_for(request(),120)
    except (TimeoutError, ConnectionError) as exc:
        add_review(page,source,str(exc) or 'Picture request timed out','model_request_failure')
        return
    if not isinstance(value,dict):
        add_review(page,source,'Picture response is not a JSON object','protocol_failure');return
    if value.get('outcome')=='review' and isinstance(value.get('reason'),str) and value['reason'].strip():
        add_review(page,source,value['reason'],candidate=value.get('candidate'));return
    if value.get('outcome')=='supplement' and isinstance(value.get('content'),str) and value['content'].strip():
        from .native import check_generated
        problem=check_generated(value['content'])
        if problem:
            add_review(page,source,problem,candidate=value['content']);return
        page.segments.append(Segment(value['content'],'',source,kind='visual'))
        return
    add_review(page,source,'Expected supplement with content or review with concrete reason','protocol_failure')
