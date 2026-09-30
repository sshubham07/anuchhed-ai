# API image (HLD §18). Model weights are cached in the hf_cache volume, not baked in.
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION} PATH="/app/.venv/bin:$PATH"

RUN useradd --create-home app
USER app
EXPOSE 8000

# HLD §18: run migrations, then serve. Model warm-up is added with the embedder (Phase 1/3).
CMD ["sh", "-c", "alembic upgrade head && uvicorn --factory samvidhan.api.main:create_app --host 0.0.0.0 --port 8000 --no-access-log"]
