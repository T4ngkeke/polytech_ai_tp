"""Source identity excludes observation geometry, generated text and reading order."""


def source_ref(page,group,parts):
    lines={l.id:l for l in page.lines};regions={r.id:r for r in page.regions}
    ids=set(group.ids);stable_regions=set();uncertain=False
    def add_region(rid,visited):
        nonlocal uncertain
        if rid in visited: uncertain=True;return
        r=regions[rid]
        if r.kind=='discovered':
            anchors=[i for i in r.source_ids if i not in regions or regions[i].kind!='background']
            if not anchors: uncertain=True
            for i in anchors:
                if i in lines: ids.add(i)
                elif i in regions:add_region(i,visited|{rid})
                else:uncertain=True
        else:
            stable_regions.add(r.id+':'+r.signature if r.signature else r.id)
    for i in group.region_ids:add_region(i,set())
    if not ids and group.region_ids and all(regions[i].kind=='background' for i in group.region_ids):
        uncertain=True
    return {'version':2,'page_no':page.page_no,'ids':sorted(ids),'regions':sorted(stable_regions),
        'source_ranges':[[i,0,len(lines[i].text)] for i in sorted(ids)],
        'parts':parts,'part':0,'identity_uncertain':uncertain}


def legacy_match(old,chunk):
    """Only prove a one-to-one native source; never infer from teacher content."""
    a=old.source_ref or {};b=chunk.source_ref or {}
    if a.get('version')!=1 or b.get('version')!=2 or b.get('identity_uncertain'):return False
    if old.source_text is None or old.source_text!=chunk.source_text:return False
    for k in ('file_sha256','page_no','part','parts'):
        if a.get(k)!=b.get(k):return False
    if sorted(a.get('ids',[]))!=b.get('ids',[]):return False
    if sorted(a.get('source_ranges',[]))!=b.get('source_ranges',[]):return False
    # Existing original region IDs still identify the exact same PDF bytes. V1
    # requested pNv boxes are not provably stable and deliberately remain protected.
    return sorted(a.get('regions',[]))==sorted(i.split(':',1)[0] for i in b.get('regions',[]))
