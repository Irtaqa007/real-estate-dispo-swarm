# Single-stage build — no gcc compilation, uses pre-built wheels only.
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SENTENCE_TRANSFORMERS_HOME=/tmp/hf_models \
    HF_HOME=/tmp/hf_models

# Runtime libs only — no gcc, no build tools needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    dnsutils \
    netcat-openbsd \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --default-timeout=300 -r requirements.txt

# Pre-download the embedding model so it's baked into the image
ARG HF_TOKEN=
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer('mixedbread-ai/mxbai-embed-large-v1', token=os.environ.get('HF_TOKEN') or None)"

ARG BUST=0
COPY . .

RUN chmod +x /app/startup.sh

EXPOSE 8000

ENTRYPOINT ["/app/startup.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]