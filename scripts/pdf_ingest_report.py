"""Summarize the enriched ingestion benchmark, keeping unreviewed results explicit."""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from scripts.docling_benchmark.core import validate_review
from scripts.docling_benchmark.report import generate, problem_counts


def summarize(root):
    manifest=json.loads((root/'manifest.json').read_text())
    cases={c['stem']:c for c in manifest['cases']}
    totals=generate(root)
    groups=defaultdict(Counter)
    per_page=[]
    source_audits=[]
    figure_coverage=[]
    identical_text_judgments=defaultdict(list)
    for path in sorted((root/'runs').glob('*/*.page.json')):
        if path.parent.name.startswith('mineru-hybrid'):continue
        page=json.loads(path.read_text());stem=path.name.removesuffix('.page.json');case=cases[stem]
        expected_digest=manifest['files'][case['file']]
        refs=[x['source_ref'] for x in page['segments']]
        source_audits.append({'run':path.parent.name,'case':stem,
            'page_identity_correct':page['file_sha256']==expected_digest and page['page_no']==case['page'],
            'segment_identity_correct':all(x.get('file_sha256')==expected_digest and x.get('page_no')==case['page'] for x in refs),
            'regions_with_bbox':len(page['metadata'].get('picture_regions',[])),
            'search_segment_scope':'whole_page' if page['implementation'].startswith('mineru-') else 'reader_group',
            'geometry_semantics':'requires visual judgment; identity checks do not prove region correctness'})
        if path.parent.name.startswith('mineru-text') and case['cohort']=='odp-rendered':
            native=json.loads((root/'conversion'/Path(case['file']).stem/'native.json').read_text())
            source_count=sum(b['kind']=='figure' for b in native['pages'][case['page']-1]['blocks'])
            raw=json.loads(path.with_name(path.name.removesuffix('.page.json')+'.raw.json').read_text())
            detected_count=sum(b['type'] in ('image','chart') for b in raw['blocks'])
            figure_coverage.append({'run':path.parent.name,'case':stem,'source_odp_figure_blocks':source_count,
                'mineru_picture_blocks':detected_count,'page_level_detected':bool(detected_count) if source_count else None,
                'limitations':'ODP IR is not an independent visual gold standard; block counts are not one-to-one'})
        review_path=path.with_suffix('.review.json');review=json.loads(review_path.read_text()) if review_path.exists() else None
        if review:
            validate_review(page,review)
            stem=path.name.removesuffix('.page.json')
            judgment=json.loads((root/'judgments'/path.parent.name/f'{stem}.json').read_text())
            payload={'checks':json.loads((root/'suite.json').read_text())['checks'][stem],
                     'searchable_text':page['searchable_text'],'reviews':page['reviews'],'errors':page['errors'],
                     'sources':[x['source_ref'] for x in page['segments']],
                     'picture_regions':[{k:b.get(k) for k in ('type','bbox','native','description','outcome','reason')}
                                        for b in page['metadata'].get('picture_regions',[])],
                     'expected_page':case['page']}
            png=(root/'previews'/f'{stem}.png').read_bytes()
            digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()+png).hexdigest()
            if judgment.get('review_input_sha256')!=digest:
                raise ValueError(f'Model judgment input changed: {path}')
        counters={'pages':1,'calls':page['calls'],'seconds':page['seconds'],'review_regions':len(page['reviews']),
                  'graded_pages':int(review is not None)}
        for request in page['metadata'].get('requests',[]):
            name=request.get('server','unknown').replace('-','_')
            counters['calls_'+name]=counters.get('calls_'+name,0)+1
            counters['tokens_'+name]=counters.get('tokens_'+name,0)+(request.get('usage',{}).get('total_tokens',0) or 0)
        if review:
            counters.update(Counter(c['result'] for c in review['checks']))
            text_hash=hashlib.sha256(page['searchable_text'].encode()).hexdigest()
            for check in review['checks']:
                identical_text_judgments[(stem,text_hash,check['check_id'])].append({
                    'run':path.parent.name,'result':check['result']})
            problems,flagged,silent=problem_counts(page,review)
            counters.update(problems=problems,flagged=flagged,silent=silent,
                silent_major=sum(e.get('severity')=='major' and not (e.get('flagged') and (page['reviews'] or page['errors'])) for e in review['added_errors']))
        for dimension,label in [('cohort',case['cohort']),('family',case['family'])]:
            groups[(path.parent.name,dimension,label)].update(counters)
        per_page.append({'run':path.parent.name,'case':stem,'family':case['family'],'cohort':case['cohort'],**counters})
    (root/'figure-detection-audit.json').write_text(json.dumps(figure_coverage,ensure_ascii=False,indent=2))
    (root/'source-identity-audit.json').write_text(json.dumps(source_audits,ensure_ascii=False,indent=2))
    (root/'grouped-summary.json').write_text(json.dumps([{'run':k[0],'dimension':k[1],'group':k[2],**v} for k,v in groups.items()],ensure_ascii=False,indent=2))
    (root/'per-page-summary.json').write_text(json.dumps(per_page,ensure_ascii=False,indent=2))
    inconsistent=[{'case':stem,'searchable_text_sha256':text_hash,'check_id':check_id,
                   'judgments':judgments}
                  for (stem,text_hash,check_id),judgments in identical_text_judgments.items()
                  if len(judgments)>1 and len({j['result'] for j in judgments})>1]
    consistency_audit={
        'method':'Compare check results only when case, check ID, and final searchable_text SHA-256 are identical.',
        'comparable_groups':sum(len(judgments)>1 for judgments in identical_text_judgments.values()),
        'inconsistent_groups':len(inconsistent),
        'affected_pages':len({entry['case'] for entry in inconsistent}),
        'inconsistencies':inconsistent}
    (root/'grading-consistency-audit.json').write_text(json.dumps(consistency_audit,ensure_ascii=False,indent=2))
    frozen_checks=json.loads((root/'checks.json').read_text())
    quality=defaultdict(lambda:{'check_kind_results':defaultdict(Counter),
                                'source_region_binding':Counter(),'source_page_binding':Counter(),
                                'original_page_available':Counter(),'figure_transcription_audit':Counter(),
                                'picture_outcomes':Counter()})
    for path in sorted((root/'runs').glob('*/*.page.json')):
        run=path.parent.name
        if run.startswith('mineru-hybrid'):continue
        stem=path.name.removesuffix('.page.json')
        page=json.loads(path.read_text())
        judgment=json.loads((root/'judgments'/run/f'{stem}.json').read_text())
        kinds={x['check_id']:x['kind'] for x in frozen_checks[stem]}
        for check in judgment['checks']:
            quality[run]['check_kind_results'][kinds[check['check_id']]][check['result']]+=1
        for field, key in [('source_region_binding','region_binding'),('source_page_binding','page_binding')]:
            quality[run][field][judgment.get('source_audit',{}).get(key,'missing')]+=1
        quality[run]['original_page_available'][str(judgment.get('image_audit',{}).get('original_page_available','missing'))]+=1
        quality[run]['figure_transcription_audit']['present' if judgment.get('image_audit',{}).get('figure_transcription') else 'missing']+=1
        for region in page['metadata'].get('picture_regions',[]):
            quality[run]['picture_outcomes'][region.get('outcome','missing')]+=1
    quality={run:{key:{k:dict(v) if key=='check_kind_results' else v
                            for k,v in value.items()} if key=='check_kind_results' else dict(value)
                  for key,value in fields.items()} for run,fields in quality.items()}
    for run,fields in quality.items():
        fields['review_records']=totals[run]['review_regions']
        fields['effective_review_flags']=totals[run]['flagged_problems']
        fields['note']='figure_transcription_audit records whether model prose is present, not a correctness score; visual_fact includes figures and tables.'
    (root/'quality-audit.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2))
    docs={Path(d['pdf_path']).stem:d for d in manifest['documents']}
    selected_uncertain=sum(docs[Path(c['file']).stem]['page_map'][c['page']-1]['check']!='anchor_unique_match'
                           for c in manifest['cases'] if c['cohort']=='odp-rendered')
    lines=['# PDF ingestion comparison and dataset expansion','',
           'This report uses saved outputs and hash-bound model assessments; no retrieval, agent answers or production release were evaluated.','',
           '## Dataset and conversion','',
           f"- 12 local ODP files converted to {sum(d['native']['pages'] for d in manifest['documents'])} PDF pages; {sum(len(d['hidden']) for d in manifest['documents'])} hidden slides and all notes excluded. Original hashes are unchanged.",
           '- PDFs are stored in `cours/pdf_files/odp_rendered/`; these are local LibreOffice exports, not teacher-supplied PDF/source pairs.',
           f"- Text anchors automatically verified {sum(d['verified_pages'] for d in manifest['documents'])} page mappings; the other 90 pages remain unverified. Of the 60 new sampled pages, {selected_uncertain} lack a unique confirming anchor. Conversion checks do not guarantee layout fidelity.",
           '- 55 historical pages plus 60 new pages: 115 total. Different years of the same course belong to one family; they are not independent held-out courses.','',
           '## Results','', '| Run | Pages | Graded | Correct checks | Missing | Wrong | Mixed | Unreviewed | Silent problems | Median seconds/page | Ornith / MinerU calls |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for run,v in totals.items():
        if run.startswith('mineru-hybrid'):continue
        c=v['checks'];lines.append(f"| {run} | {v['pages']} | {v['graded_pages']} | {c.get('correct',0)} | {c.get('missing',0)} | {c.get('wrong',0)} | {c.get('mixed',0)} | {c.get('unreviewed',0)} | {v['silent_problems']} | {v['median_seconds']:.2f} | {sum(1 for p in (root/'runs'/run).glob('*.page.json') for req in json.loads(p.read_text())['metadata'].get('requests',[]) if req.get('server')=='ornith')} / {sum(1 for p in (root/'runs'/run).glob('*.page.json') for req in json.loads(p.read_text())['metadata'].get('requests',[]) if req.get('server')=='mineru-vlm')} |")
    complete=len(per_page)==690 and all(x['graded_pages'] for x in per_page)
    fig_source=sum(x['source_odp_figure_blocks']>0 for x in figure_coverage if x['run']=='mineru-text-round1')
    fig_detect=sum(x['source_odp_figure_blocks']>0 and x['page_level_detected'] for x in figure_coverage if x['run']=='mineru-text-round1')
    lines.append('')
    lines+=['Review and provenance:']
    for run in sorted(quality):
        q=quality[run]
        lines.append(f"- {run}: review records {q['review_records']}, assessment-confirmed flagged problems {q['effective_review_flags']}; "
                     f"model source-region assessment correct / wrong / unknown / missing = "
                     f"{q['source_region_binding'].get('correct',0)} / {q['source_region_binding'].get('wrong',0)} / "
                     f"{q['source_region_binding'].get('unknown',0)} / {q['source_region_binding'].get('missing',0)}.")
    lines.append('')
    lines.append('Correct visual-fact checks (figures and tables; 95 checks/round): '
                 + '; '.join(f"{run} {quality[run]['check_kind_results'].get('visual_fact',{}).get('correct',0)}"
                            for run in sorted(quality)) + '.')
    lines.append('Most model source-region assessments are unknown; correct file hashes and PDF page bindings do not establish correct region coordinates.')
    lines.append('')
    if all(v['graded_pages']==115 for key,v in totals.items() if not key.startswith('mineru-hybrid')):
        checks=json.loads((root/'checks.json').read_text())
        for round_no in (1,2):
            gains=losses=0
            for stem in checks:
                outputs={}
                for variant in ('mineru-text','mineru-figures'):
                    p=root/'runs'/f'{variant}-round{round_no}'/f'{stem}.page.review.json'
                    outputs[variant]={x['check_id']:x['result'] for x in json.loads(p.read_text())['checks']}
                for cid,base in outputs['mineru-text'].items():
                    enriched=outputs['mineru-figures'][cid]
                    gains+=base!='correct' and enriched=='correct'
                    losses+=base=='correct' and enriched!='correct'
            lines.append(f'- Figure supplementation in round {round_no} versus mineru-text: {gains} checks changed to correct, {losses} changed from correct; 47 additional Ornith calls.')
        lines+=['', f"Assessment consistency: {consistency_audit['comparable_groups']} comparable groups have identical page, check and searchable_text; {consistency_audit['inconsistent_groups']} groups have conflicting assessments across {consistency_audit['affected_pages']} pages (see grading-consistency-audit.json).",
                'Some model added_errors label the same string both correct and incorrect. Severe-error counts include grading noise and require item-by-item review. Figure supplementation shows no reliable, stable net benefit across two rounds; mineru-text remains the simpler offline candidate.',
                'Worker has more correct checks, but round-to-round variation and review workload on new pages are substantial. These model assessments do not establish student retrieval or citation quality.']
    lines+=['', 'All original page previews are saved; see quality-audit.json for model image_audit of image availability and whether visual descriptions were returned. '
            'Visual checks for figures and tables are summarized separately by visual_fact as correct / missing / wrong / mixed / unreviewed. '
            'There is no independent, reliable quantitative score for visual transcription; a detected figure or generated description does not establish correctness.',
            '',f"The source ODP reader marked figures on {fig_source} new sampled pages; MinerU detected at least one figure on {fig_detect} of them. This measures page-level coverage, not coverage of every figure object.",
            '',f"Status: {'two rounds of all three groups and model assessments complete' if complete else 'incomplete; missing or ungraded items cannot be interpreted as zero errors'}.",'',
            '## Interpretation limits','',
            '- Ornith generated new checks from original pages before seeing extraction outputs; these checks were then frozen. Ornith also graded outputs and supplied visual supplements. Correlated errors are possible; this is not an independent human gold standard.',
            '- Native labels and generated descriptions are stored separately as native / description in raw.blocks; original PDF pages remain available. Generated descriptions are not source text.',
            '- Content grading uses searchable_text; image retention, visual transcription and source regions are recorded separately in judgments image_audit / source_audit.',
            '- Model-call statistics use existing counters; postprocessing includes extraction time and requests from the same MinerU round. Tokens from different models are not treated as equivalent cost.',
            '- Worker prepares the whole PDF for each sampled page. Timings compare this experiment path, not whole-document throughput or student query latency.',
            '- Historical/new pages and course-family summaries are in grouped-summary.json; per-page data in per-page-summary.json; side-by-side evidence in comparison.html.',
            '- Small development-set differences do not establish generalization or production acceptance. Repeated runs on the same page are not independent samples.']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')
    html=root/'comparison.html'
    if html.exists():html.write_text(html.read_text().replace('28-page development review','115-page PDF ingestion comparison'))
    return complete

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);a=p.parse_args();summarize(a.output)
