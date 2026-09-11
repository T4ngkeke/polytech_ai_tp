"""Narrow content checks for the reviewed fixtures; not a semantic quality score.

False means this check did not establish preservation, and requires review. A
true result never replaces the full image/content audit. File hashes are listed
with CLI output so the checked artifact is unambiguous.
"""
import json
import re
import sys
from pathlib import Path


def check_page(row):
    groups=[g for p in row.get('audit',{}).get('pages',[]) for g in p['groups']]
    text='\n'.join(g['text'] for g in groups)
    compact=re.sub(r'\s+','',text)
    key=row['file'],row['page']
    if key==('main-2.pdf',55):
        formulas=[g['text'] for g in groups if g.get('kind')=='formula' and g.get('visual')]
        normal=''.join(formulas).replace('−','-')
        normal=re.sub(r'\s+|\\left|\\right','',normal)
        return {'formula_structure_preserved':bool(re.search(r'2\^\{E-biais\}',normal)) and
                    bool(re.search(r'\(-1\)\^(?:\{S\}|S)',normal)),
                'bit_width_labels_present':all(x in text for x in ('1 bit','8 bit','23 bit'))}
    if key==('main-3.pdf',27):return {'original_space_symbol_preserved':'plus␣de␣memoire' in text}
    if key==('main-3.pdf',54):return {
        'resize_and_append_present':all(x in compact for x in ('if(taille==capacite)','realloc(arr,','arr[taille]=valeur;','taille++;')),
        'code_group_preserved':any(g['kind']=='code' for g in groups)}
    if key==('Heritage.pdf',8):
        # UML is a figure: a short description plus the class names as printed.
        figures=[g for g in groups if g.get('kind')=='figure' and (g.get('visual') or {}).get('description','').strip()]
        return {'figure_described':bool(figures),
                'class_names_kept':all(x in text for x in ('Employee','Manager','Programmer'))}
    return {'manual_review_required':True}


if __name__=='__main__':
    records=json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps([{'file':r['file'],'page':r['page'],'round':r.get('round',1),
        'source_sha256':r.get('source_file_sha256'),'checks':check_page(r)} for r in records],ensure_ascii=False,indent=2))
