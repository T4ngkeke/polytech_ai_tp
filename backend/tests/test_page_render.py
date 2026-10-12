"""Where a cited block sits on its page, for the preview's highlight."""
from backend.app.services.page_render import block_box


def test_pdf_blocks_use_minerus_region_in_thousandths():
    ref = {'bbox': None, 'bbox_basis': 'unknown',
           'legacy_ref': {'mineru_bbox_per_mille': [51, 253, 931, 529], 'block_index': 1}}
    assert block_box(ref) == [0.051, 0.253, 0.931, 0.529]


def test_odp_blocks_use_native_fractions():
    ref = {'bbox': [0.0512, 0.2, 0.9, 0.45], 'bbox_basis': 'native_odf_geometry'}
    assert block_box(ref) == [0.0512, 0.2, 0.9, 0.45]


def test_unknown_or_broken_regions_give_none():
    assert block_box(None) is None
    assert block_box({'bbox': None, 'bbox_basis': 'unknown'}) is None
    assert block_box({'legacy_ref': {'mineru_bbox_per_mille': [10, 20, 30]}}) is None
    assert block_box({'legacy_ref': {'mineru_bbox_per_mille': [500, 20, 400, 90]}}) is None  # x1 < x0
    # Slightly outside the page is clamped to it.
    assert block_box({'legacy_ref': {'mineru_bbox_per_mille': [-5, 0, 1003, 990]}}) == [0.0, 0.0, 1.0, 0.99]
