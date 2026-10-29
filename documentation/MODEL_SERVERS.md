> Part of the Edu-LLM documentation. Back to [README](../README.md).

# Model servers

This file says how the main model server runs: Qwen3.8 Flash-Next, served as `flash-next`. The release and dev stacks both use it. MinerU is in [PDF_INGESTION.md](PDF_INGESTION.md).

## What runs

| | Value |
| --- | --- |
| Container | `flash-next`, restart policy `unless-stopped` |
| Image | `ghcr.io/salient-data/qwen38-next-dgx@sha256:d55a2740651064f6c8aba20bc9ada104ae9df0a793cc87d0c170d312aa052b4d` (vLLM 0.32.1 nightly `8e2beaa3` with the recipe's patches) |
| Recipe | [salient-data/qwen38-next-dgx-recipe](https://github.com/salient-data/qwen38-next-dgx-recipe), commit `7432036`, cloned in `/home/spark-1/llm_inference/` |
| Checkpoint | `nvidia/Qwen3.8-Flash-Next-NVFP4`, snapshot `fc694b54fb0174e0913e6adf86691ef85a4ead47` |
| Reached at | network `edullm-models`, alias `m4-flashnext-v030`, port 8000; on the host `127.0.0.1:18301` |
| Limits | 65,536 tokens per request, 4 requests at once, `--gpu-memory-utilization 0.77` |

The admin page stores the address `http://m4-flashnext-v030:8000/v1` and the name `flash-next`. A new server must keep this alias, so that no setting changes. The server also answers to the old name `ornith`, so that settings not yet changed keep working; the name in its replies and metrics is `flash-next`.

## Settings that differ from the recipe

The recipe's `launch.sh` writes the whole `docker run` command. We change these parts:

- `--served-model-name flash-next ornith --max-model-len 65536 --max-num-seqs 4 --gpu-memory-utilization 0.77 --tool-call-parser qwen3_coder`.
- `--kv-cache-dtype auto` (BF16) instead of the recipe's `fp8_e4m3`. On 2026-10-27, BF16 gave the same speed and better benchmark results (v3 coverage 126/130 and citation precision 76%, against 118–124/130 and 69–72% with FP8). It holds 439k tokens, which is enough for 4 × 65k.
- `--prefix-match-unit 8 --enable-mamba-shared-prefix-checkpoint`: a prefix-cache hit resumes at the end of the previous prompt. Without this, every agent step computes again about 2,000 tokens. The unit must divide every cacheable KV block size. With BF16 the block is 1088 tokens, so 8 works and 12 fails at start. With FP8 the block is 2112 tokens, so 12 works.
- `--enable-prompt-tokens-details`: the answers report cached prompt tokens, which Grafana shows.
- `--scheduling-policy priority`: requests with a lower `priority` value go first. Chat requests send none (0); picture captions send 10, so a student does not wait behind a document's captions. Speed is the same as without it (measured 2026-10-29, see [PICTURE_CAPTIONS.md](PICTURE_CAPTIONS.md)).
- Name, network alias, port `127.0.0.1:18301:8000`, `--host 0.0.0.0`.
- The snapshot's files link into the shared Hugging Face store, so the whole hub folder `/home/spark-1/.cache/huggingface/hub` is mounted read-only at `/hub`. The model path is `/hub/models--nvidia--Qwen3.8-Flash-Next-NVFP4/snapshots/<snapshot>`.

The recipe also loads the attention, linear-attention, shared-expert and output layers as FP8 at start (`QWEN38_FP8_DENSE`). We keep this.

## Start or replace it

1. Print the recipe's command with our settings, then change the name, network, port and mount as listed above:

   ```sh
   cd /home/spark-1/llm_inference/qwen38-next-dgx-recipe/recipe
   FORCE=1 ./launch.sh --prod --detach --print -- \
     --served-model-name flash-next --max-model-len 65536 --max-num-seqs 4 \
     --gpu-memory-utilization 0.77 --tool-call-parser qwen3_coder \
     --prefix-match-unit 8 --enable-mamba-shared-prefix-checkpoint \
     --enable-prompt-tokens-details --kv-cache-dtype auto \
     --scheduling-policy priority
   ```

   `launch.sh` takes one served name: add `ornith` after `flash-next` in the
   printed command.

2. Stop the running server first. Two servers do not fit in memory, and they would share the alias. The release has no model until the new one is ready, about 4 minutes.
3. Ready means `Application startup complete` in `docker logs`, and `curl http://127.0.0.1:18301/v1/models` answers.
4. Set the restart policy: `docker update --restart unless-stopped <container>`.

## Fallback

`ornith-salient-bf16kv` is stopped and kept. It is the same server without priority scheduling and with only the old name `ornith`. To use it: `docker stop flash-next && docker start ornith-salient-bf16kv`, then set `PICTURE_CAPTION_PRIORITY` empty in the admin page (a server without priority scheduling refuses requests that carry one) and the model names back to `ornith`.

`ornith-pmu8` is also stopped and kept: the RadixArk NVFP4 checkpoint on the image `qwen38-flash-dgx:v0.30` (vLLM 0.30, MTP 2, BF16 KV cache, `--prefix-match-unit 8`), about 15% slower per research answer, with equal content scores on 2026-10-27. Docker does not start a container again after a reboot if it was stopped by hand, so two servers never run at once.
