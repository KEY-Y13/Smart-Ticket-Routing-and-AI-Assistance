FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS retrieval
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /app
COPY requirements-retrieval.txt ./
RUN --mount=type=bind,source=artifacts/wheels-retrieval-linux,target=/wheels,readonly pip install --no-index --find-links=/wheels -r requirements-retrieval.txt && pip check && python -c "import torch,faiss; print(torch.__version__)"
RUN useradd --uid 10001 --create-home app && mkdir -p artifacts/indexes && chown -R app:app artifacts
COPY rag ./rag
USER 10001:10001
CMD ["python", "-m", "uvicorn", "rag.worker:app", "--host", "0.0.0.0", "--port", "8011", "--no-access-log"]
