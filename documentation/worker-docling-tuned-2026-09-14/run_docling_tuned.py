"""Docling with its optional stages turned on, on the 28 pages of the two earlier runs. GPU, serial.

Variants (no training, configuration only):
  A        formula + code enrichment (CodeFormulaV2)
  C-small  A + picture description with Docling's default small VLM (SmolVLM-256M)
  C-api    A + picture description through the already running local vLLM (pictures only)
"""
import argparse,hashlib,json,platform,time,traceback
from importlib.metadata import version
from pathlib import Path
from docling.document_converter import DocumentConverter,PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions,PictureDescriptionApiOptions,smolvlm_picture_description
from docling.datamodel.accelerator_options import AcceleratorOptions,AcceleratorDevice
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1]
PROMPT=('Describe this figure for a student who cannot see it. If it is a diagram, give the exact text of every box '
        'and state every arrow or link: which element points to which, and what the link means. '
        'If it is a table or a chart, give its values. Do not add facts that are not in the figure.')
p=argparse.ArgumentParser();p.add_argument('--variant',choices=['A','C-small','C-api'],required=True);args=p.parse_args()
folder=OUT/f'docling-{args.variant}';folder.mkdir(exist_ok=True)
manifest=json.loads((OUT/'manifest.json').read_text())
options=PdfPipelineOptions(do_formula_enrichment=True,do_code_enrichment=True,
 accelerator_options=AcceleratorOptions(device=AcceleratorDevice.CUDA,num_threads=8),document_timeout=240)
if args.variant!='A':
 options.do_picture_description=True;options.generate_picture_images=True;options.images_scale=2
 if args.variant=='C-small':
  options.picture_description_options=smolvlm_picture_description.model_copy(update={'prompt':PROMPT})
 else:
  options.enable_remote_services=True
  options.picture_description_options=PictureDescriptionApiOptions(url='http://127.0.0.1:18001/v1/chat/completions',prompt=PROMPT,timeout=180,
   params={'model':'ornith','max_tokens':2048,'temperature':0,'chat_template_kwargs':{'enable_thinking':False}})
config={'variant':args.variant,'python':platform.python_version(),'packages':{k:version(k) for k in ['docling','docling-core','docling-ibm-models','torch','transformers']},
        'options':json.loads(options.model_dump_json()),'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
if (folder/'config.json').exists() and json.loads((folder/'config.json').read_text())!=config:raise RuntimeError('Config changed; use a separate output folder')
(folder/'config.json').write_text(json.dumps(config,indent=2))
converter=DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=options)})
start=time.monotonic();converter.initialize_pipeline(InputFormat.PDF);load=round(time.monotonic()-start,3)
print(json.dumps({'pipeline_load_seconds':load}),flush=True)
for case in manifest['cases']:
 name,n,stem=case['file'],case['page'],case['stem'];out=folder/f'{stem}.result.json'
 if out.exists():print('Already recorded',stem,flush=True);continue
 source=ROOT/'cours'/name
 assert hashlib.sha256(source.read_bytes()).hexdigest()==manifest['files'][name]
 start=time.monotonic();record={'file':name,'page':n,'source_sha256':manifest['files'][name]}
 try:
  result=converter.convert(source,page_range=(n,n),raises_on_error=False)
  record.update(status=str(result.status),errors=[str(e) for e in result.errors])
  (folder/f'{stem}.json').write_text(json.dumps(result.document.export_to_dict(),ensure_ascii=False,indent=2))
  (folder/f'{stem}.md').write_text(result.document.export_to_markdown())
 except Exception as e:
  record.update(status='exception',error=str(e),traceback=traceback.format_exc())
 record['seconds']=round(time.monotonic()-start,3);out.write_text(json.dumps(record,ensure_ascii=False,indent=2));print(json.dumps(record,ensure_ascii=False),flush=True)
