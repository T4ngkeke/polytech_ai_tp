"""Geometry evidence, not automatic deletion. Repeated content stays visible."""
from collections import Counter
from dataclasses import replace
import hashlib
import json
import pymupdf

SIGNATURE_VERSION=1
# Exact signatures may be added only with a checked source image and review record.
# Empty is intentional: recurrence by itself never populates this registry.
CONFIRMED_TEMPLATES={}
STYLE=('type','items','color','fill','width','lineCap','lineJoin','dashes',
       'closePath','even_odd','fill_opacity','stroke_opacity')


def _plain(value):
    if isinstance(value,dict):return {k:_plain(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [_plain(v) for v in value]
    if isinstance(value,(pymupdf.Point,pymupdf.Rect,pymupdf.Quad,pymupdf.Matrix)):
        return [_plain(v) for v in value]
    if isinstance(value,bytes):return value.hex()
    return value


def annotate_regions(page,regions):
    drawings=page.get_drawings()
    images=page.get_image_info(hashes=True)
    result=[]
    for region in regions:
        box=pymupdf.Rect(region.bbox)
        tolerance=pymupdf.Rect(box.x0-.02,box.y0-.02,box.x1+.02,box.y1+.02)
        if region.kind=='image':
            pieces=[{k:_plain(i.get(k)) for k in ('bbox','transform','digest','width','height','colorspace')}
                    for i in images if tolerance.contains(pymupdf.Rect(i['bbox']))]
        else:
            pieces=[{k:_plain(d.get(k)) for k in STYLE}
                    for d in drawings if tolerance.contains(pymupdf.Rect(d['rect']))
                    and (pymupdf.Rect(d['rect']).get_area() >= page.rect.get_area()*.9) == (region.kind=='background')]
        # Preserve painter order: same paths painted in another order may look different.
        payload=[SIGNATURE_VERSION,list(page.rect),page.rotation,region.kind,pieces]
        signature=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        result.append(replace(region,signature=signature))
    return tuple(result)


def repeat_hints(pages,confirmed=None):
    confirmed=CONFIRMED_TEMPLATES if confirmed is None else confirmed
    counts=Counter(s for p in pages for s in {r.signature for r in p.regions if r.signature})
    total=len(pages)
    return [replace(p,regions=tuple(replace(r,occurrence_pages=counts[r.signature],total_pages=total,
        template_hint=('confirmed_decoration' if r.signature in confirmed else
            'suspected_template' if r.signature and counts[r.signature]>=.8*total else None))
        for r in p.regions)) for p in pages]
