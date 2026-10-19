"""Candidate models for scripts/retrieval_eval.py, run with transformers in a GPU container.

  embed  OUT MODEL NAME   writes OUT/vectors-NAME.json (chunks and questions)
  rerank OUT MODEL NAME   writes OUT/rerank-NAME.json (scores of OUT/candidates.json)

Self-contained (no backend import), so it runs in the vLLM image:

  docker run --rm --gpus all --memory 6g -v $PWD:/w:ro -v $PWD/OUT:/out \
    -v ~/.cache/huggingface:/root/.cache/huggingface -w /w --entrypoint python3 \
    nvcr.io/nvidia/vllm:26.07-py3 scripts/retrieval_eval_models.py embed /out Qwen/Qwen3-Embedding-0.6B qwen3-emb-0.6b

Both embedding candidates (Qwen3-Embedding, harrier-oss-v1) pool the last token
and want a one-sentence task before the query, none before the passage. The
Qwen3 reranker answers yes or no; the score is the probability of yes.
"""
import json
import sys
import time
from pathlib import Path

import torch
import torch.nn.functional as F
from transformers import AutoModel, AutoModelForCausalLM, AutoTokenizer

TASK = 'Given a student question about a course, retrieve the course passages that answer it'
BATCH = 16
MAX_TOKENS = 1024


def last_token_pool(hidden, mask):
    if bool(mask[:, -1].all()):                    # left padding
        return hidden[:, -1]
    lengths = mask.sum(dim=1) - 1
    return hidden[torch.arange(hidden.shape[0], device=hidden.device), lengths]


@torch.no_grad()
def encode(model, tokenizer, texts):
    vectors = []
    for start in range(0, len(texts), BATCH):
        batch = tokenizer(texts[start:start + BATCH], padding=True, truncation=True,
                          max_length=MAX_TOKENS, return_tensors='pt').to(model.device)
        hidden = model(**batch).last_hidden_state
        vectors += F.normalize(last_token_pool(hidden, batch['attention_mask']).float(), dim=-1).tolist()
    return vectors


def embed(out: Path, name: str, label: str):
    chunks = [json.loads(line) for line in (out / 'chunks.jsonl').read_text().splitlines()]
    questions = [json.loads(line) for line in (out / 'questions.jsonl').read_text().splitlines()]
    tokenizer = AutoTokenizer.from_pretrained(name, padding_side='left')
    model = AutoModel.from_pretrained(name, torch_dtype=torch.bfloat16).cuda().eval()
    started = time.monotonic()
    chunk_vectors = encode(model, tokenizer, [c['text'] for c in chunks])
    query_vectors = encode(model, tokenizer, [f"Instruct: {TASK}\nQuery: {q['question']}" for q in questions])
    (out / f'vectors-{label}.json').write_text(json.dumps({
        'model': name, 'task': TASK,
        'chunks': {c['id']: v for c, v in zip(chunks, chunk_vectors)},
        'queries': {q['id']: v for q, v in zip(questions, query_vectors)}}))
    print(f'{label}: {len(chunks)} chunks, {len(questions)} questions in {time.monotonic() - started:.0f} s')


PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and '
          'the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'


@torch.no_grad()
def rerank(out: Path, name: str, label: str):
    chunks = {c['id']: c for c in map(json.loads, (out / 'chunks.jsonl').read_text().splitlines())}
    questions = {q['id']: q for q in map(json.loads, (out / 'questions.jsonl').read_text().splitlines())}
    candidates = json.loads((out / 'candidates.json').read_text())
    tokenizer = AutoTokenizer.from_pretrained(name, padding_side='left')
    model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.bfloat16).cuda().eval()
    yes, no = tokenizer.convert_tokens_to_ids('yes'), tokenizer.convert_tokens_to_ids('no')
    started = time.monotonic()
    result = {}
    for qid, ids in candidates.items():
        texts = [f"{PREFIX}<Instruct>: {TASK}\n<Query>: {questions[qid]['question']}\n"
                 f"<Document>: {chunks[i]['rerank_text']}{SUFFIX}" for i in ids]
        scores = []
        for start in range(0, len(texts), BATCH):
            batch = tokenizer(texts[start:start + BATCH], padding=True, truncation=True,
                              max_length=2048, return_tensors='pt').to(model.device)
            logits = model(**batch).logits[:, -1, :]
            pair = torch.stack([logits[:, no], logits[:, yes]], dim=1).float()
            scores += torch.softmax(pair, dim=1)[:, 1].tolist()
        result[qid] = dict(zip(ids, scores))
    (out / f'rerank-{label}.json').write_text(json.dumps(result))
    pairs = sum(map(len, candidates.values()))
    print(f'{label}: {pairs} pairs in {time.monotonic() - started:.0f} s')


if __name__ == '__main__':
    command, out, name, label = sys.argv[1], Path(sys.argv[2]), sys.argv[3], sys.argv[4]
    {'embed': embed, 'rerank': rerank}[command](out, name, label)
