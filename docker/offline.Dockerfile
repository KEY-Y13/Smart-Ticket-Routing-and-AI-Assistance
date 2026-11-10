FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-runtime.txt ./
RUN --mount=type=bind,source=artifacts/wheels-linux,target=/wheels,readonly pip install --no-index --find-links=/wheels -r requirements-runtime.txt && pip check
RUN useradd --uid 10001 --create-home app
COPY backend ./backend
COPY nlp ./nlp
COPY rag ./rag
COPY scripts ./scripts
COPY alembic ./alembic
COPY data/knowledge ./data/knowledge
COPY alembic.ini pyproject.toml ./
COPY frontend/dist ./frontend/dist
RUN mkdir -p /app/artifacts/governance /app/artifacts/operations && chown -R app:app /app/artifacts
USER 10001:10001
EXPOSE 8010
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
