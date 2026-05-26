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

# Copy the rest of the backend source code
COPY backend/ ./backend/

# Expose port
EXPOSE 8000

# Start Uvicorn
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
