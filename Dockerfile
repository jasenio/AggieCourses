FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/jasenio/AggieCourses"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    HF_HOME=/opt/huggingface

WORKDIR /app

COPY requirements.txt ./
# The public PyPI torch wheel now carries CUDA runtime dependencies. This app
# runs semantic search on CPU, so use the smaller CPU-only wheel explicitly.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch \
    && pip install --no-cache-dir -r requirements.txt

# Bake the CPU embedding and annotation cross-encoder models into the image so
# semantic retrieval and human annotation pooling work with HF_HUB_OFFLINE=1.
RUN mkdir -p /opt/huggingface \
    && HF_HUB_OFFLINE=0 python -c "from sentence_transformers import CrossEncoder, SentenceTransformer; SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2'); CrossEncoder('cross-encoder/ms-marco-MiniLM-L6-v2')"

COPY backend ./backend
COPY frontend ./frontend
COPY scripts ./scripts
COPY data ./data
COPY data/ltr ./data/ltr

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app /opt/huggingface

USER appuser
EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
