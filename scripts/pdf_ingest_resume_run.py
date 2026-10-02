"""Resume the historical page benchmark while ignoring ephemeral model permission IDs.
Every substantive field and the complete code fingerprint must still match.
"""
import argparse
import asyncio
import json
from pathlib import Path
from scripts.docling_benchmark import run as benchmark


def stable_model_snapshot(value):
    value=json.loads(json.dumps(value))
    for model in value.get('data',[]):
        model.pop('created',None)
        for permission in model.get('permission',[]):
            permission.pop('id',None)
            permission.pop('created',None)
    return value


def checked_freeze(folder,config):
    path=folder/'config.json'
    if not path.exists():
        return benchmark_original_freeze(folder,config)
    saved=json.loads(path.read_text())
    current=dict(config);prior=dict(saved)
    current['models']=stable_model_snapshot(current['models'])
    prior['models']=stable_model_snapshot(prior['models'])
    if current!=prior:
        keys=[k for k in set(current)|set(prior) if current.get(k)!=prior.get(k)]
        raise ValueError(f'Frozen run changed outside transient model permissions: {keys}')
    # Keep the original config byte-for-byte, including the first observed permissions.

benchmark_original_freeze=benchmark.freeze
benchmark.freeze=checked_freeze


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--variant',choices=benchmark.VARIANT_CHOICES,required=True)
    p.add_argument('--round',type=int,choices=[1,2],required=True)
    p.add_argument('--base-url',default='http://127.0.0.1:18001/v1')
    p.add_argument('--model',default='ornith')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--suite',type=Path,required=True)
    p.add_argument('--pages',nargs='*')
    asyncio.run(benchmark.run(p.parse_args()))

if __name__=='__main__':main()
