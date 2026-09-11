"""Source identity excludes observation geometry, generated text and reading order.

Version 3 is the one definition shared by chunks and review records. Version 1/2
references stay readable for compatibility; they are never silently rewritten.
"""
import hashlib
import json
import re

_UNKNOWN = object()  # legacy (v1) region without a recorded geometry signature


def uses_v3(reader_version: str) -> bool:
    match = re.fullmatch(r'region-reader-v(\d+)\.(\d+)', reader_version or '')
    return bool(match) and (int(match[1]), int(match[2])) >= (1, 5)


def _expand(page, ids, region_ids):
    """Native IDs plus stable original region IDs; discovered regions resolve to anchors."""
    lines = {l.id: l for l in page.lines}
    regions = {r.id: r for r in page.regions}
    if any(i not in lines for i in ids) or any(i not in regions for i in region_ids):
        raise ValueError('Source IDs must belong to this page')
    ids = set(ids)
    stable = {}
    uncertain = False

    def add_region(rid, visited):
        nonlocal uncertain
        if rid in visited:
            uncertain = True
            return
        r = regions[rid]
        if r.kind == 'discovered':
            anchors = [i for i in r.source_ids if i not in regions or regions[i].kind != 'background']
            if not anchors:
                uncertain = True
            for i in anchors:
                if i in lines:
                    ids.add(i)
                elif i in regions:
                    add_region(i, visited | {rid})
                else:
                    uncertain = True
        else:
            stable[r.id] = r.signature

    for i in region_ids:
        add_region(i, set())
    if not ids and region_ids and all(regions[i].kind == 'background' for i in region_ids):
        uncertain = True
    return lines, ids, stable, uncertain


def source_ref(page, group, parts):
    """Version 2, kept for pages from older readers."""
    lines, ids, stable, uncertain = _expand(page, group.ids, group.region_ids)
    return {'version': 2, 'page_no': page.page_no, 'ids': sorted(ids),
            'regions': sorted(rid + ':' + sig if sig else rid for rid, sig in stable.items()),
            'source_ranges': [[i, 0, len(lines[i].text)] for i in sorted(ids)],
            'parts': parts, 'part': 0, 'identity_uncertain': uncertain}


def build_source_ref(page, ids, region_ids, *, file_sha256, scope='range') -> dict:
    """Version 3 source: file, physical page, native ranges and original regions."""
    if scope not in ('range', 'page'):
        raise ValueError('scope must be range or page')
    lines, ids, stable, uncertain = _expand(page, list(ids), list(region_ids))
    if not ids and not stable:
        uncertain = True
    return {'version': 3, 'file_sha256': file_sha256, 'page_no': page.page_no, 'scope': scope,
            'ids': sorted(ids), 'source_ranges': [[i, 0, len(lines[i].text)] for i in sorted(ids)],
            'regions': [{'id': rid, 'signature': stable[rid]} for rid in sorted(stable)],
            'identity_uncertain': uncertain}


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _identity(ref):
    # Explicit whitelist: no generated text, heading, order, crop or split fields.
    return [3, ref['file_sha256'], ref['page_no'], ref.get('scope', 'range'), bool(ref['identity_uncertain']),
            sorted(ref['ids']), sorted(list(r) for r in ref['source_ranges']),
            sorted(([r['id'], r['signature']] for r in ref['regions']), key=lambda r: r[0])]


def source_group_key(ref: dict) -> str:
    if not isinstance(ref, dict) or ref.get('version') != 3:
        raise ValueError('Group identity needs a version 3 source')
    return _digest(_identity(ref))


def source_chunk_key(ref: dict, source_text) -> str:
    return _digest(['chunk-v3', source_group_key(ref), ref.get('part', 0), ref.get('parts', 1), source_text])


def _normalized(ref):
    if not isinstance(ref, dict) or ref.get('identity_uncertain'):
        return None
    version = ref.get('version')
    try:
        if version == 3:
            regions = {r['id']: r['signature'] for r in ref['regions']}
        elif version == 2:
            regions = {}
            for value in ref.get('regions', []):
                rid, _, signature = value.partition(':')
                regions[rid] = signature or None
        elif version == 1:
            regions = {rid: _UNKNOWN for rid in ref.get('regions', [])}
        else:
            return None
        return (ref.get('file_sha256'), ref.get('page_no'), ref.get('scope', 'range'),
                frozenset(ref.get('ids', [])), regions)
    except (KeyError, TypeError, AttributeError):
        return None


def compare_sources(left, right) -> str:
    """exact / disjoint / overlap, or uncertain whenever evidence is missing."""
    a, b = _normalized(left), _normalized(right)
    if a is None or b is None or not a[0] or a[0] != b[0]:
        return 'uncertain'
    if a[1] != b[1]:
        return 'disjoint'
    if not (a[3] or a[4]) or not (b[3] or b[4]):
        return 'uncertain'
    shared = a[4].keys() & b[4].keys()
    for rid in shared:
        left_sig, right_sig = a[4][rid], b[4][rid]
        # Same original ID is the same picture only if both geometries agree; a v1
        # record without a signature is compared by ID only against another v1 record.
        if (left_sig is _UNKNOWN) != (right_sig is _UNKNOWN) or left_sig != right_sig:
            return 'uncertain'
    if not (a[3] & b[3]) and not shared:
        return 'disjoint'
    if a[3] == b[3] and a[4].keys() == b[4].keys() and a[2] == b[2]:
        return 'exact'
    return 'overlap'


def match_legacy_source(old_ref, new_ref, *, old_source_text, new_source_text) -> bool:
    """Only prove a one-to-one native source; never fuzzy, never from teacher content."""
    if not isinstance(old_ref, dict) or not isinstance(new_ref, dict):
        return False
    old_version, new_version = old_ref.get('version'), new_ref.get('version')
    if old_version not in (1, 2) or new_version not in (2, 3) or new_version <= old_version:
        return False
    if old_ref.get('identity_uncertain') or new_ref.get('identity_uncertain'):
        return False
    if old_source_text is None or old_source_text != new_source_text:
        return False
    for key in ('file_sha256', 'page_no', 'part', 'parts'):
        if old_ref.get(key) != new_ref.get(key):
            return False
    if sorted(old_ref.get('ids', [])) != sorted(new_ref.get('ids', [])):
        return False
    if sorted(map(list, old_ref.get('source_ranges', []))) != sorted(map(list, new_ref.get('source_ranges', []))):
        return False
    old, new = _normalized(old_ref), _normalized(new_ref)
    if old is None or new is None or old[4].keys() != new[4].keys():
        return False
    # V1 has no signature: the same original ID in the same file bytes is the proof.
    # V1 requested pNv boxes never equal an original region ID and stay protected.
    return old_version == 1 or all(old[4][rid] == new[4][rid] for rid in old[4])


def legacy_match(old, chunk):
    """Chunk-level wrapper used by ingestion."""
    return match_legacy_source(old.source_ref or {}, chunk.source_ref or {},
                               old_source_text=old.source_text, new_source_text=chunk.source_text)
