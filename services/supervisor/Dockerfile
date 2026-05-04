# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/agentradar-core/pyproject.toml packages/agentradar-core/
COPY packages/agentradar-store/pyproject.toml packages/agentradar-store/
COPY services/supervisor/pyproject.toml services/supervisor/

COPY packages/ packages/
COPY services/supervisor/ services/supervisor/

RUN uv sync --no-dev --package agentradar-supervisor --frozen

# ---------- Runtime --------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN groupadd --system --gid 1000 agentradar \
    && useradd --system --uid 1000 --gid agentradar --no-create-home agentradar

WORKDIR /app

COPY --from=builder --chown=agentradar:agentradar /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER agentradar

CMD ["python", "-m", "agentradar_supervisor"]