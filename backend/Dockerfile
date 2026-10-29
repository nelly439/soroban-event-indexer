# Image for both the indexer and the API (they differ only in the command).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY soroban_indexer ./soroban_indexer
RUN pip install --no-deps .

RUN useradd --system --uid 10001 --home-dir /data indexer \
    && mkdir -p /data && chown indexer /data
USER indexer
WORKDIR /data
VOLUME ["/data"]

ENV DATABASE_URL=sqlite:////data/soroban_events.db \
    API_HOST=0.0.0.0 \
    API_PORT=8000

EXPOSE 8000
ENTRYPOINT ["soroban-indexer"]
CMD ["serve"]
