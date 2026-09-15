FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/opt/venv/bin:${PATH}

# Compilers are available only while constructing the virtual environment.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
COPY DataHandling/requirements-docker.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt


FROM python:3.12-slim AS runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:${PATH}

# ffmpeg supports audio, poppler renders PDFs, and libgomp is required by the
# ONNX runtime used by the active Chroma retrieval path.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        libgomp1 \
        poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app

# Explicit runtime allow-list: do not copy .env, credentials, tests,
# documentation, deployment scripts, or local database content.
COPY DataHandling/app ./app
COPY DataHandling/data ./data
COPY DataHandling/docscanner ./docscanner
COPY DataHandling/src ./src
COPY DataHandling/upload ./upload
COPY DataHandling/server.py ./server.py

RUN mkdir -p \
    audio_files \
    config \
    db/vector \
    output \
    received_audio \
    transcripts

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read()"]

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
