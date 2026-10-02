# The isolated slide renderer for M2-A.
#
# It holds LibreOffice, poppler and a Python interpreter, and nothing else: no
# application code, no database driver, no model client. Run it with no network
# and with one task directory mounted:
#
#   docker run --rm --network none --read-only \
#     --tmpfs /tmp:rw,size=256m --security-opt no-new-privileges \
#     --cpus 2 --memory 3g --pids-limit 256 \
#     --user "$(id -u):$(id -g)" \
#     -v "<task_dir>:/task" edullm-slide-renderer:1 /task
#
# The docker socket is never mounted. The API image does not get LibreOffice;
# only this component does.
FROM debian:bookworm-slim

# python3 rather than python3-minimal: the entry point needs json, hashlib,
# zipfile and resource, and python3-minimal ships none of them. The first image
# built for M2-A failed at startup with ModuleNotFoundError: No module named
# 'json'.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress \
        libreoffice-calc \
        libreoffice-math \
        libreoffice-core \
        poppler-utils \
        python3 \
        fonts-dejavu-core \
        fonts-liberation2 \
        fonts-noto-core \
        fonts-freefont-ttf \
    && rm -rf /var/lib/apt/lists/*

# Fail the build rather than the first task if the standard library the entry
# point needs is not there.
RUN python3 -c "import json, hashlib, zipfile, resource, signal, subprocess, re; print('stdlib ok')" \
    && soffice --version \
    && pdfinfo -v 2>&1 | head -1

# The font set is part of the result: a different set renders different pages.
# It is recorded here and in the run report.
LABEL org.edullm.renderer.fonts="dejavu-core,liberation2,noto-core,freefont"
LABEL org.edullm.renderer.protocol="1"

COPY backend/worker/odf_package.py /opt/renderer/odf_package.py
COPY backend/renderer/entrypoint.py /opt/renderer/entrypoint.py

# The profile lives in the task directory, so the image itself stays read-only.
ENV HOME=/tmp
ENTRYPOINT ["python3", "/opt/renderer/entrypoint.py"]
CMD ["/task"]
