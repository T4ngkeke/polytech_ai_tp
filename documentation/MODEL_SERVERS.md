> Part of the Edu-LLM documentation. Back to [README](../README.md).

# Model servers

This file says how the main model server runs: Qwen3.8 Flash-Next, served as `flash-next`. The release and dev stacks both use it. It also says how all model servers check their key ([The key](#the-key)). MinerU is in [PDF_INGESTION.md](PDF_INGESTION.md).

## What runs

| | Value |
| --- | --- |
| Container | `flash-next`, restart policy `unless-stopped` |
| Image | `ghcr.io/salient-data/qwen38-next-dgx@sha256:d55a2740651064f6c8aba20bc9ada104ae9df0a793cc87d0c170d312aa052b4d` (vLLM 0.32.1 nightly `8e2beaa3` with the recipe's patches) |
| Recipe | [salient-data/qwen38-next-dgx-recipe](https://github.com/salient-data/qwen38-next-dgx-recipe), commit `7432036`, cloned in `/home/spark-1/llm_inference/` |
| Checkpoint | `nvidia/Qwen3.8-Flash-Next-NVFP4`, snapshot `fc694b54fb0174e0913e6adf86691ef85a4ead47` |
| Reached at | network `edullm-models`, alias `m4-flashnext-v030`, port 8000; on the host `127.0.0.1:18301` |
| Limits | 131,072 tokens per request (since 2026-11-18; 65,536 before), 4 requests at once, a fixed KV cache of 10,400,000,000 bytes (`--kv-cache-memory`, since 2026-11-19), `--gpu-memory-utilization 0.72` |

The admin page stores the address `http://m4-flashnext-v030:8000/v1` and the name `flash-next`. A new server must keep this alias, so that no setting changes. Since 2026-11-02 the server no longer answers to the old name `ornith`; a request with that name gets a 404.

## Settings that differ from the recipe

The recipe's `launch.sh` writes the whole `docker run` command. We change these parts:

- `--served-model-name flash-next --max-model-len 131072 --max-num-seqs 4 --gpu-memory-utilization 0.72 --tool-call-parser qwen3_coder`. With 0.77 the machine ran out of memory: the server takes 93.7 GiB, and with MinerU, embeddings, the reranker and the host processes the total was about 121 GiB of 121.7. There were two OOM kills on 2026-11-19, and after the reboot the server could not start, because less than 93.7 GiB was free. With 0.72 the KV cache is 8.5 GiB, 297k tokens (2026-11-19), and about 12 GiB of memory stays free.
- `--kv-cache-memory 10400000000` (9.7 GiB, 340,395 tokens, 2.6 full-length requests; the server takes 87.0 GiB and about 15 GiB of memory stays free): a fixed KV cache size. With only `--gpu-memory-utilization`, vLLM gives the KV cache what is left of that share after its own measure of the weights and other memory, and on this machine (CPU and GPU share one memory) that measure changes from one start to the next. On 2026-11-19 a start at 0.73 measured 71.4 GiB instead of 77.4 GiB, so the KV cache became 550k tokens, the server took 93.1 GiB instead of 85.8, and only 6 GiB of memory was left. A fixed size keeps the server at the same size at every start. `--gpu-memory-utilization 0.72` stays, because vLLM still checks at start that this share of memory is free.
- `--kv-cache-dtype auto` (BF16) instead of the recipe's `fp8_e4m3`. On 2026-10-27, BF16 gave the same speed and better benchmark results (v3 coverage 126/130 and citation precision 76%, against 118–124/130 and 69–72% with FP8). The KV cache size depends on the memory that is free when the server starts: between 438k and 509k tokens at the starts from 2026-10-29 to 2026-11-02. This is enough for 4 × 65k (262k).
- `--prefix-match-unit 8 --enable-mamba-shared-prefix-checkpoint`: a prefix-cache hit resumes at the end of the previous prompt. Without this, every agent step computes again about 2,000 tokens. The unit must divide every cacheable KV block size. With BF16 the block is 1088 tokens, so 8 works and 12 fails at start. With FP8 the block is 2112 tokens, so 12 works.
- `--enable-prompt-tokens-details`: the answers report cached prompt tokens, which Grafana shows.
- `--scheduling-policy priority`: requests with a lower `priority` value go first. Chat requests send none (0); picture captions send 10, so a student does not wait behind a document's captions. Speed is the same as without it (measured 2026-10-29, see [PICTURE_CAPTIONS.md](PICTURE_CAPTIONS.md)). vLLM's description of the `priority` field says that a server without priority scheduling returns an error for any priority other than 0. We have not tried this.
- Name, network alias, port `127.0.0.1:18301:8000`, `--host 0.0.0.0`.
- The key check: the mount, the key file and `--middleware` listed in [The key](#the-key).
- The snapshot's files link into the shared Hugging Face store, so the whole hub folder `/home/spark-1/.cache/huggingface/hub` is mounted read-only at `/hub`. The model path is `/hub/models--nvidia--Qwen3.8-Flash-Next-NVFP4/snapshots/<snapshot>`.

The recipe also loads the attention, linear-attention, shared-expert and output layers as FP8 at start (`QWEN38_FP8_DENSE`). We keep this.

## Start or replace it

1. Print the recipe's command with our settings, then change the name, network, port and mount as listed above:

   ```sh
   cd /home/spark-1/llm_inference/qwen38-next-dgx-recipe/recipe
   FORCE=1 ./launch.sh --prod --detach --print -- \
     --served-model-name flash-next --max-model-len 131072 --max-num-seqs 4 \
     --gpu-memory-utilization 0.72 --kv-cache-memory 10400000000 --tool-call-parser qwen3_coder \
     --prefix-match-unit 8 --enable-mamba-shared-prefix-checkpoint \
     --enable-prompt-tokens-details --kv-cache-dtype auto \
     --scheduling-policy priority
   ```

2. Stop the running server first. Two servers do not fit in memory, and they would share the alias. The release has no model until the new one is ready, about 4 minutes.
3. Ready means `Application startup complete` in `docker logs`, and `curl -H "Authorization: Bearer $KEY" http://127.0.0.1:18301/v1/models` answers (without the key: `401`).
4. Set the restart policy: `docker update --restart unless-stopped <container>`.

## The key

All model servers check one shared key: `flash-next`, `edullm-embeddings`, `edullm-reranker` and `edullm-mineru-vlm`. Without it, any process on the host or container on the network `edullm-models` could use the models and fill the queue in front of the students.

vLLM's own `--api-key` is not enough. It checks only paths that start with `/v1`, `/v2`, `/inference` or `/cohere`. Other paths stay open: `/invocations` runs a full chat or embedding request, and the reranker also answers on `/rerank`, `/score` and `/pooling` (checked on vLLM 0.24 and 0.32, 2026-11-19). So each server loads our own check, [`model_servers/edullm_key_check.py`](../model_servers/edullm_key_check.py). It refuses every request without `Authorization: Bearer <key>` with `401`, except `/health`, `/ping`, `/metrics` and `/version`. Prometheus reads `/metrics` without a key. A server started without the key stops at start, so it never runs open.

**Where the key lives.** On the host, outside the repository, readable by its owner only:

```sh
mkdir -p ~/.local/share/edullm-models && cd ~/.local/share/edullm-models
cp <repo>/model_servers/edullm_key_check.py .
umask 077 && printf 'EDULLM_MODEL_API_KEY=%s\n' "$(openssl rand -hex 32)" > key.env
```

**Each server's `docker run`** gets three more parts (the file is mounted into the image's Python packages, so it can be imported):

```sh
docker run ... \
  -v ~/.local/share/edullm-models/edullm_key_check.py:/usr/local/lib/python3.12/dist-packages/edullm_key_check.py:ro \
  --env-file ~/.local/share/edullm-models/key.env \
  <image> vllm serve ... --middleware edullm_key_check.KeyCheck
```

**Who sends it.**

- The dev and release stacks: the admin page, LLM Config, "Shared key of the model servers" (stored as `LLM_API_KEY`). Every server uses it unless its row has "Use its own key" on. The Test button of each row shows "Key refused (401)" when a server does not accept the key. The worker passes the MinerU key to MinerU's client as `MINERU_VL_API_KEY`.
- The benchmark (`scripts/m4_evaluate.py`): `M4_API_KEY`, for the model, the vision calls and the reranker.
- Anything else that calls a model server, for example a test script: the same header.

**Switching the key on, or changing it.** Set the new key in the admin pages of both stacks first: a server without the check ignores the header. Then start the servers again with the check. To change the key, write the new one in `key.env`, set it in both admin pages, and start the servers again. The stacks fail with `401` between the two steps, so do both at once.

## Fallback

The containers from before the key (2026-11-19) are kept stopped, with restart `no`: `flash-next-prekey`, `edullm-embeddings-prekey`, `edullm-reranker-prekey` and `edullm-mineru-vlm-prekey`. To go back, stop the new container, rename the old one to its name, set its restart policy and start it; then empty the API Key fields in the admin pages or keep them (a server without the check ignores the key).

Apart from these, no stopped spare server is kept. To go back to an older setting, start a new container with the steps above. The 0.72 container without the key, from before the fixed KV size, is `flash-next-prekey`.

- Without `--scheduling-policy priority`: first set `PICTURE_CAPTION_PRIORITY` empty in the admin page, because such a server refuses requests that carry a priority.
- The image `qwen38-flash-dgx:v0.30` is still on the machine. It runs the RadixArk NVFP4 checkpoint (vLLM 0.30, MTP 2, BF16 KV cache, `--prefix-match-unit 8`): about 15% slower per research answer, with equal content scores on 2026-10-27.

Docker does not start a container again after a reboot if it was stopped by hand, so a stopped spare never runs at the same time as the server.
