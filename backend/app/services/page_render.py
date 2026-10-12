"""Images of course pages, for the vision model (view_image) and for the
student's citation preview. The file path never leaves the server."""
from __future__ import annotations


def render_page_png(path, kind: str, page_no: int) -> tuple[bytes, int, int]:
    """PNG of one physical PDF page (kind 'pdf_preview'), or of a PNG/JPEG
    preview, at most 1400 px on the long side. Returns (png, width, height)."""
    import pymupdf
    with pymupdf.open(path) as image_doc:
        target = image_doc[page_no - 1] if kind == 'pdf_preview' else image_doc[0]
        factor = min(1.0, 1400 / max(target.rect.width, target.rect.height))
        if kind == 'pdf_preview':
            factor = min(1.5, 1400 / max(target.rect.width, target.rect.height))
        pix = target.get_pixmap(matrix=pymupdf.Matrix(factor, factor), alpha=False)
        return pix.tobytes('png'), pix.width, pix.height


def block_box(source_ref: dict | None) -> list[float] | None:
    """Where a block sits on its page, as [x0, y0, x1, y1] fractions of the
    page width and height, or None when unknown.

    ODP slides store native geometry as fractions (bbox, 'native_odf_geometry');
    PDF blocks keep MinerU's region in thousandths (legacy_ref).
    """
    ref = source_ref or {}
    box = None
    if ref.get('bbox_basis') == 'native_odf_geometry' and ref.get('bbox'):
        box = ref['bbox']
    elif isinstance(ref.get('legacy_ref'), dict) and ref['legacy_ref'].get('mineru_bbox_per_mille'):
        box = [v / 1000 for v in ref['legacy_ref']['mineru_bbox_per_mille']]
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    x0, y0, x1, y1 = (min(1.0, max(0.0, v)) for v in (x0, y0, x1, y1))
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(v, 4) for v in (x0, y0, x1, y1)]
