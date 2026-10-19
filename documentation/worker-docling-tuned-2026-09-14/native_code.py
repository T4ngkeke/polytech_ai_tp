"""Step 1 of the native-code adapter (project .venv, PyMuPDF). No model.

For each Docling item labelled `code`, take the PDF's own characters inside its box and rebuild
the lines: one line per baseline, each character placed at its column, so line breaks and
indentation come back. The column step is the median distance between neighbouring character
origins in monospace spans. Spans in a proportional font are placed as whole text. If the box
has an empty vertical strip at least 4 columns wide, each side is rebuilt on its own (left first).
Writes <folder>/native-code.json: {stem: {self_ref: text}}.
"""
import json,statistics,sys
from pathlib import Path
import pymupdf
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1]
def mono(s):
 w=[c['bbox'][2]-c['bbox'][0] for c in s['chars'] if c['c'].strip()]
 return len(w)>1 and statistics.pstdev(w)<0.05*statistics.mean(w)
def rebuild(page,box):
 H=page.rect.height;r=pymupdf.Rect(box['l']-3,H-box['t']-3,box['r']+3,H-box['b']+3)
 spans=[]
 for b in page.get_text('rawdict')['blocks']:
  for l in b.get('lines',[]):
   for s in l['spans']:
    chars=[c for c in s['chars'] if r.contains(pymupdf.Point((c['bbox'][0]+c['bbox'][2])/2,(c['bbox'][1]+c['bbox'][3])/2))]
    if chars:spans.append({'y':chars[0]['origin'][1],'x0':chars[0]['origin'][0],'x1':chars[-1]['bbox'][2],'chars':chars})
 if not spans:return None
 steps=[b['origin'][0]-a['origin'][0] for s in spans if mono(s) for a,b in zip(s['chars'],s['chars'][1:]) if b['origin'][0]>a['origin'][0]]
 cw=statistics.median(steps or [c['bbox'][2]-c['bbox'][0] for s in spans for c in s['chars'] if c['c'].strip()])
 # Split at an empty vertical strip (two code columns side by side).
 ink=sorted((c['bbox'][0],c['bbox'][2]) for s in spans for c in s['chars'] if c['c'].strip());edge,cut=ink[0][1],None
 for a,b in ink[1:]:
  if a-edge>=4*cw:cut=(edge+a)/2;break
  edge=max(edge,b)
 if cut is not None:
  left=[s for s in spans if s['x0']<cut];right=[s for s in spans if s['x0']>=cut]
  # Aligned comments share their code line's baseline; true side-by-side columns mostly do not.
  shared=sum(any(abs(r['y']-l['y'])<1.5 for l in left) for r in right)
  if left and right and shared<=len(right)/2:return layout(left,cw)+'\n\n'+layout(right,cw)
 return layout(spans,cw)
def layout(spans,cw):
 xmin=min(s['x0'] for s in spans)
 lines=[]
 for s in sorted(spans,key=lambda s:(s['y'],s['x0'])):
  if lines and abs(lines[-1][0]-s['y'])<1.5:lines[-1][1].append(s)
  else:lines.append([s['y'],[s]])
 text=[];gaps=[b[0]-a[0] for a,b in zip(lines,lines[1:])];step=statistics.median(gaps) if len(gaps)>1 else None
 for i,(y,ss) in enumerate(lines):
  if step and i and y-lines[i-1][0]>1.6*step:text.append('')
  # Column step of this line (a block can mix code sizes); indentation is measured from the block's left edge.
  own=[b['origin'][0]-a['origin'][0] for s in ss if mono(s) for a,b in zip(s['chars'],s['chars'][1:]) if b['origin'][0]>a['origin'][0]]
  lw=statistics.median(own) if own else cw
  col=lambda x:max(0,round((x-xmin)/lw))
  out=''
  for s in sorted(ss,key=lambda s:s['x0']):
   if mono(s):
    # Spaces only where the text layer has one (or at a span start); the column decides how many.
    start,space=True,False
    for c in s['chars']:
     if not c['c'].strip():space=True;continue
     if start or space:
      k=col(c['origin'][0])
      if k>len(out):out+=' '*(k-len(out))
      elif space and out and not out.endswith(' '):out+=' '
     out+=c['c'];start=space=False
   else:
    k=col(s['x0']);t=''.join(c['c'] for c in s['chars']).strip()
    out=out+' '*(k-len(out)) if k>len(out) else (out+' ' if out and not out.endswith(' ') else out);out+=t
  text.append(out.rstrip())
 return '\n'.join(text)
folder=OUT/sys.argv[1];manifest=json.loads((OUT/'manifest.json').read_text());result={}
for case in manifest['cases']:
 doc=json.loads((folder/f"{case['stem']}.json").read_text());got={}
 with pymupdf.open(ROOT/'cours'/case['file']) as pdf:
  for t in doc['texts']:
   if t['label']!='code':continue
   p=t['prov'][0];assert p['bbox']['coord_origin']=='BOTTOMLEFT'
   new=rebuild(pdf[p['page_no']-1],p['bbox'])
   if new:got[t['self_ref']]=new
 result[case['stem']]=got
(folder/'native-code.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(sys.argv[1],sum(len(v) for v in result.values()),'code items rebuilt')
