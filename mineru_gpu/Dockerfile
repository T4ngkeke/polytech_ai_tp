# MinerU's small models on the GPU (backend/worker/pdf_mineru_gpu.py).
# Build from the repository root:
#   docker build -f mineru_gpu/Dockerfile -t edullm-mineru-gpu:3.4.5 .
# The NVIDIA image gives torch, torchvision and OpenCV with CUDA for this GPU;
# the other packages are the worker's MinerU 3.4.5 set, put first on the path.
FROM nvcr.io/nvidia/vllm:26.07-py3

# The base image pins its own packages (PIP_CONSTRAINT). These go to their own
# folder, as in the tested set-up, so its pins do not apply.
COPY mineru_gpu/requirements.txt /tmp/mineru-requirements.txt
RUN PIP_CONSTRAINT= pip install --no-cache-dir --no-deps --target /opt/mineru-sp -r /tmp/mineru-requirements.txt \
    && rm /tmp/mineru-requirements.txt

COPY backend/worker/__init__.py backend/worker/pdf_mineru.py backend/worker/pdf_mineru_gpu.py /app/backend/worker/

ENV PYTHONPATH=/opt/mineru-sp:/app \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    MINERU_MODEL_SOURCE=local \
    MINERU_DEVICE_MODE=cuda \
    MINERU_LOG_LEVEL=WARNING \
    HOME=/tmp
WORKDIR /app
USER 1000:1000
ENTRYPOINT ["python", "-m", "backend.worker.pdf_mineru_gpu"]
