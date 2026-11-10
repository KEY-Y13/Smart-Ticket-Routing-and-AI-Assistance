# syntax=docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
FROM node:24.13.0-alpine@sha256:cd6fb7efa6490f039f3471a189214d5f548c11df1ff9e5b181aa49e22c14383e AS frontend
WORKDIR /web
COPY frontend/package*.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --registry=https://registry.npmjs.org --no-fund
COPY frontend/index.html frontend/vite.config.js ./
COPY frontend/src ./src
RUN npm run build

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-runtime.txt ./
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-runtime.txt
RUN useradd --uid 10001 --create-home app
COPY backend ./backend
COPY nlp ./nlp
COPY rag ./rag
COPY scripts ./scripts
COPY alembic ./alembic
COPY data/knowledge ./data/knowledge
COPY alembic.ini pyproject.toml ./
COPY --from=frontend /web/dist ./frontend/dist
RUN mkdir -p /app/artifacts/governance /app/artifacts/operations && chown -R app:app /app/artifacts
USER 10001:10001
EXPOSE 8010
CMD ["python", "-m", "uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8010", "--no-access-log"]
