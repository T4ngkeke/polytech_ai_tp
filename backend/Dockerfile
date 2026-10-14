FROM python:3.12-slim

# Set working directory to the project root so imports like `backend.app...` work
WORKDIR /app

# Prevent Python from writing pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies if required (e.g., for building some C extensions)
# RUN apt-get update && apt-get install -y gcc ...

# Copy the requirements file and install dependencies
COPY backend/requirements.txt ./backend/
RUN pip install --no-cache-dir -r backend/requirements.txt

# PDF worker only (WITH_MINERU=1): MinerU 3.4.5 in its own interpreter,
# /opt/mineru/bin/python (PDF_MINERU_PYTHON), with CPU torch for its local
# layout and OCR models (six: an import of its OCR code that 3.4.5 does not
# declare). The models and mineru.json are mounted at run time.
ARG WITH_MINERU=0
RUN if [ "$WITH_MINERU" = "1" ]; then \
      apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
      && rm -rf /var/lib/apt/lists/* \
      && python -m venv /opt/mineru \
      && /opt/mineru/bin/pip install --no-cache-dir \
         --extra-index-url https://download.pytorch.org/whl/cpu \
         "mineru[pipeline]==3.4.5" "mineru-vl-utils>=1.0.5,<2" six; \
    fi

# Copy the rest of the backend source code
COPY backend/ ./backend/

# Expose port
EXPOSE 8000

# Start Uvicorn
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
