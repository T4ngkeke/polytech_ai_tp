"""Historical grading audit. Original evidence is never overwritten."""
import json
from .suite import HISTORY, OUTPUT, ROOT, sha


def audit():
    first=ROOT/'documentation/worker-docling-comparison-2026-09-14'
    findings=[]
    path=first/'worker-current/main-2-p6.json'
    row=json.loads(path.read_text())
    content='\n'.join(c['content'] for c in row['chunks'])
    native='\n'.join(c.get('source_text') or '' for c in row['chunks'])
    if '22h30' not in content and '22h30' in native:
        findings.append(dict(page='main-2-p6',check_id='main-2-p6/total',previous='correct',corrected='missing',
            reviewer='Codex',reviewer_kind='model',evidence='22h30 exists only in source_text, not searchable content.',
            artifact=str(path.relative_to(ROOT)),artifact_sha256=sha(path)))
    path=first/'worker-current/Heritage-p8.json'
    content='\n'.join(c['content'] for c in json.loads(path.read_text())['chunks'])
    if 'from parent to child' in content:
        findings.append(dict(page='Heritage-p8',reviewer='Codex',reviewer_kind='model',
            issue='Correct inheritance names coexist with an incorrect claim that the triangle points from parent to child.',
            status='added factual error; previous all-correct page claim is invalid',
            artifact=str(path.relative_to(ROOT)),artifact_sha256=sha(path)))
    for folder in ('docling-C-api+native','docling-C-small+native'):
        for row in json.loads((HISTORY/folder/'content-review.json').read_text()):
            if row['scored_here']:continue
            name=row['page']+'.md'
            old=first/'docling-formula'/name
            if not old.exists():old=ROOT/'documentation/worker-docling-unseen-2026-09-14/docling-formula'/name
            new=HISTORY/folder/name
            if new.read_bytes()!=old.read_bytes():
                findings.append(dict(page=row['page'],variant=folder,issue='Changed output inherited old review',
                    old_sha256=sha(old),new_sha256=sha(new),status='requires new review'))
    return findings


if __name__=='__main__':
    OUTPUT.mkdir(parents=True,exist_ok=True)
    findings=audit()
    (OUTPUT/'historical-grading-audit.json').write_text(json.dumps(findings,ensure_ascii=False,indent=2))
    print(json.dumps(findings,ensure_ascii=False,indent=2))
