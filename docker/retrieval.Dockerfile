# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS retrieval
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked --mount=type=cache,target=/var/lib/apt/lists,sharing=locked apt-get update && apt-get install -y --no-install-recommends libgomp1
COPY requirements-retrieval.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install torch==2.10.0 --index-url https://download.pytorch.org/whl/cpu && pip install -r requirements-retrieval.txt
RUN useradd --uid 10001 --create-home app && mkdir -p artifacts/indexes && chown -R app:app artifacts
COPY rag ./rag
USER 10001:10001
CMD ["python", "-m", "uvicorn", "rag.worker:app", "--host", "0.0.0.0", "--port", "8011", "--no-access-log"]
