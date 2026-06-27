# Stage 1: Install dependencies
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir --default-timeout=1000 -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install system dependencies:
# - libpq-dev: PostgreSQL client library for asyncpg
# - curl: for healthcheck and connectivity testing
# - dnsutils: provides nslookup, dig for DNS debugging
# - netcat-openbsd: provides nc for TCP connectivity tests
# - iproute2 or net-tools: provides ip, route, netstat for network config
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    dnsutils \
    netcat-openbsd \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Pre-download the embedding model at build time so startup is fast
# and the model is baked into the image (no download on first run).
# Pass --build-arg HF_TOKEN=xxx when building to authenticate with the HF Hub.
ARG HF_TOKEN=
ENV SENTENCE_TRANSFORMERS_HOME=/app/models
RUN python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer('mixedbread-ai/mxbai-embed-large-v1', token=os.environ.get('HF_TOKEN') or None)"

COPY . .

# Make the startup script executable
RUN chmod +x /app/startup.sh

EXPOSE 8000

# Entrypoint runs network diagnostics, then exec's the CMD
ENTRYPOINT ["/app/startup.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
