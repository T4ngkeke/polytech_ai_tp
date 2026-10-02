"""Read-only corpus check using the dedicated renderer compose; no application services."""
import argparse,hashlib,json,os,subprocess,time,sys
from pathlib import Path
REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
from backend.worker.odp_reader import read_odp
from backend.worker.slide_renderer import (prepare_deck_task,RenderSettings,collect_result,
 pdf_page_texts,verify_preview_mapping,apply_preview_mapping,attach_preview_assets,object_task_for_block)

def main():
    parser=argparse.ArgumentParser(description='Isolated M2-A ODP preview smoke check; not human content acceptance.')
    parser.add_argument('--output-root', type=Path, required=True, help='New dedicated task directory')
    parser.add_argument('--corpus', type=Path, default=REPOSITORY / 'cours/odp_files')
    args=parser.parse_args()
    root=args.output_root.resolve();root.mkdir(parents=True, exist_ok=False)
    image_id=subprocess.check_output(['docker','image','inspect','edullm-slide-renderer:1','--format','{{.Id}}'],text=True).strip()
    (root/'environment.json').write_text(json.dumps({'image_id':image_id,'dpi':110,'network':'none','project':'edullm-m2a-test','human_review':'pending'},indent=2))
    rows=[]
    for source in sorted(args.corpus.glob('*.odp')):
        start=time.monotonic();d=read_odp(source);before=hashlib.sha256(source.read_bytes()).hexdigest()
        task=prepare_deck_task(d,source,root,settings=RenderSettings(want_page_images=True),task_id=source.stem)
        (task.task_dir/'native.json').write_text(json.dumps(d.to_dict(),ensure_ascii=False,indent=2))
        env=dict(os.environ,TASK_DIR=str(task.task_dir),RENDERER_UID=str(os.getuid()),RENDERER_GID=str(os.getgid()))
        cmd=['docker','compose','-p','edullm-m2a-test','-f',str(REPOSITORY/'docker-compose.renderer-test.yml'),'run','--rm','renderer']
        with (task.task_dir/'compose.log').open('w') as log:
            run=subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=660,cwd=REPOSITORY)
        row={'file':source.name,'source_sha256':before,'native':d.totals(),'parse_quality':d.parse_quality(),
             'hidden':list(d.hidden_slide_numbers),'group':'later_corpus' if source.name.startswith(('StructureDonnees','Theorie')) else 'development',
             'container_exit':run.returncode}
        try:
            result=collect_result(task)
            mapping=verify_preview_mapping(d,pdf_page_texts(result.pdf.path))
            apply_preview_mapping(d,mapping);assets=attach_preview_assets(d,result)
            row.update(status=result.status,render_seconds=result.seconds,renderer_version=result.renderer_version,
                       soffice_version=result.soffice_version,verified_pages=mapping.verified_pages,
                       needs_review=mapping.needs_review,preview_images=sum(o.kind == 'page_png' for o in result.outputs),attached_assets=len(assets))
            (task.task_dir/'mapping.json').write_text(json.dumps(mapping.to_dict(),ensure_ascii=False,indent=2))
        except Exception as exc:
            row.update(status='failed',error=str(exc))
        row['source_unchanged']=before==hashlib.sha256(source.read_bytes()).hexdigest()
        row['wall_seconds']=round(time.monotonic()-start,2)
        rows.append(row);(root/'summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        print(json.dumps(row,ensure_ascii=False),flush=True)

    if not rows or any(row['status'] != 'ok' or not row['source_unchanged'] for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
