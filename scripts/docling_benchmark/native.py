"""Recover native characters; uncertainty is explicit, not repaired by generation."""
from dataclasses import dataclass, field
import math
import re
import statistics
import pymupdf


@dataclass
class Recovery:
    text: str
    source_ref: dict
    issues: list[str] = field(default_factory=list)


def checked_box(page, bbox):
    if len(bbox)!=4 or not all(math.isfinite(x) for x in bbox):
        raise ValueError('Invalid coordinates')
    r=pymupdf.Rect(bbox)
    if r.is_empty or not page.rect.contains(r):
        raise ValueError('Box must be inside the visible page')
    return r


def check_generated(text):
    if '\ufffd' in text or 'formula-not-decoded' in text:
        return 'Unresolved extracted characters'
    tokens=text.split()
    for n in range(1,9):
        for i in range(max(0,len(tokens)-n*12+1)):
            if tokens[i:i+n]*12==tokens[i:i+n*12]:
                return 'Repetitive generation requires review'
    return None


def rebuild(page,bbox):
    r=checked_box(page,bbox)
    ref={'version':1,'page_no':page.number+1,'bbox':list(r),'coord_origin':'TOPLEFT'}
    if page.rotation:
        return Recovery(page.get_textbox(r),ref,['Rotated code needs review'])
    spans=[]
    for block in page.get_text('rawdict')['blocks']:
        for line in block.get('lines',[]):
            if line.get('dir',(1,0))!=(1,0):
                if r.intersects(pymupdf.Rect(line['bbox'])):
                    return Recovery(page.get_textbox(r),ref,['Nonhorizontal text needs review'])
                continue
            for span in line['spans']:
                chars=[c for c in span['chars'] if r.contains(pymupdf.Point((c['bbox'][0]+c['bbox'][2])/2,(c['bbox'][1]+c['bbox'][3])/2))]
                if chars and any(c['c'].strip() for c in chars):
                    spans.append((chars[0]['origin'][1],span['size'],chars))
    if not spans:return Recovery('',ref,['No native characters in region'])
    # Duplicate PDF glyphs are ambiguous: retain raw text rather than silently deduplicating.
    keys=[(round(c['origin'][0],2),round(c['origin'][1],2)) for _,_,cs in spans for c in cs if c['c'].strip()]
    if len(keys)!=len(set(keys)):
        return Recovery(page.get_textbox(r),ref,['Overlapping native glyphs'])
    widths=[c['bbox'][2]-c['bbox'][0] for _,_,cs in spans for c in cs if c['c'].strip()]
    cw=statistics.median(widths)
    if cw<=0:return Recovery(page.get_textbox(r),ref,['Invalid character widths'])
    # Split only gutters separating whole source spans. Do not split string literals or inline spaces.
    intervals=sorted((cs[0]['bbox'][0],cs[-1]['bbox'][2]) for _,_,cs in spans)
    edge=intervals[0][1];cut=None
    for left,right in intervals[1:]:
        if left-edge>=8*cw:cut=(edge+left)/2;break
        edge=max(edge,right)
    groups=[spans]
    issues=[]
    if cut is not None:
        left=[s for s in spans if s[2][0]['origin'][0]<cut]
        right=[s for s in spans if s[2][0]['origin'][0]>=cut]
        if left and right:
            aligned=all(any(abs(a[0]-b[0])<min(a[1],b[1])*0.15 for a in left) for b in right)
            if aligned:
                issues.append('Aligned separated spans: columns versus inline comments uncertain')
            if not aligned or min(len(left),len(right))>1:
                groups=[left,right]
    output=[]
    for group in groups:
        xmin=min(c['origin'][0] for _,_,cs in group for c in cs)
        lines=[]
        for y,size,cs in sorted(group,key=lambda s:(s[0],s[2][0]['origin'][0])):
            if lines and abs(y-lines[-1][0])<min(size,lines[-1][1])*0.15:
                lines[-1][2].extend(cs)
            else:lines.append([y,size,list(cs)])
        rows=[]
        for _,size,cs in lines:
            cs.sort(key=lambda c:c['origin'][0])
            steps=[b['origin'][0]-a['origin'][0] for a,b in zip(cs,cs[1:]) if 0<b['origin'][0]-a['origin'][0]<size]
            step=statistics.median(steps) if steps else cw
            text=' '*max(0,round((cs[0]['origin'][0]-xmin)/step))
            prev=None
            for c in cs:
                if prev is not None:
                    gap=round((c['origin'][0]-prev['origin'][0])/step)-1
                    if gap>0:text+=' '*gap
                text+=c['c'];prev=c
            rows.append(text.rstrip())
        output.append('\n'.join(rows))
    return Recovery('\n\n'.join(output),ref,issues)
