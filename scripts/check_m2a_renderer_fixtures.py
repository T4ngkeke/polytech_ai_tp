"""Isolated renderer integration checks; fixture imports are test-only."""
import argparse,json,os,subprocess,sys
from pathlib import Path
REPOSITORY=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPOSITORY))
from backend.tests.odp_fixtures import write_course_deck
from backend.worker.odp_reader import read_odp
from backend.worker.slide_renderer import *

def main():
    parser=argparse.ArgumentParser(description='Synthetic notes/formula and real ODG render checks')
    parser.add_argument('--output-root', type=Path, required=True)
    args=parser.parse_args()
    root=args.output_root.resolve();root.mkdir(parents=True,exist_ok=False)
    source=write_course_deck(root/'synthetic.odp');doc=read_odp(source)
    tasks=[prepare_deck_task(doc,source,root,settings=RenderSettings(want_page_images=True),task_id='synthetic-notes-formula')]
    source=REPOSITORY/'cours/odp_files/Theorie_Des_Langages_2022-2023.odp';d=read_odp(source)
    page,block=next((p,b) for p,b in d.all_blocks() if b.preview_required and b.preview_scope=='object' and b.preview_object_id)
    tasks.append(object_task_for_block(block,source,root,source_slide_no=page.source_slide_no,
      settings=RenderSettings(want_page_images=True),task_id='real-object'))
    rows=[]
    for task in tasks:
        env=dict(os.environ,TASK_DIR=str(task.task_dir),RENDERER_UID=str(os.getuid()),RENDERER_GID=str(os.getgid()))
        with (task.task_dir/'compose.log').open('w') as log:
            run=subprocess.run(['docker','compose','-p','edullm-m2a-test','-f',str(REPOSITORY/'docker-compose.renderer-test.yml'),'run','--rm','renderer'],env=env,stdout=log,stderr=subprocess.STDOUT,timeout=660,cwd=REPOSITORY)
        row={'task':task.task_id,'exit_code':run.returncode,'task_metadata':task.to_dict()}
        try:
            result=collect_result(task);row.update(status=result.status,page_count=result.page_count,seconds=result.seconds,outputs=[o.to_dict() for o in result.outputs])
            if task.task_id.startswith('synthetic'):
                texts=pdf_page_texts(result.pdf.path)
                row['text']=texts
                row['hidden_source_pages']=doc.hidden_slide_numbers
                joined='\n'.join(texts)
                if not ('√' in joined and 'x' in joined and 'y' in joined):
                    raise RuntimeError('formula missing from synthetic preview; check libreoffice-math')
                if 'Notes secretes' in joined or 'Notes visibles' in joined:
                    raise RuntimeError('notes leaked into preview')
                if result.page_count != 3:
                    raise RuntimeError('hidden slide was not excluded')
            else:
                row['native_source']=block.source_ref.to_dict()
        except Exception as exc:
            row.update(status='failed',error=str(exc))
        rows.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
    (root/'summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))

    if any(row['status'] != 'ok' for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
