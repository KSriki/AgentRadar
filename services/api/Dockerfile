# syntax=docker/dockerfile:1.7

# ---------- Builder stage --------------------------------------------------
# Resolve and install the Python workspace into a self-contained venv.
FROM python:3.12-slim AS builder

# Install uv (the fast Python resolver) from the official distroless image
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Copy workspace metadata first so this layer caches when only source changes
COPY pyproject.toml uv.lock ./
COPY packages/agentradar-core/pyproject.toml packages/agentradar-core/
COPY packages/agentradar-store/pyproject.toml packages/agentradar-store/
COPY services/api/pyproject.toml services/api/

# Install ONLY production deps for the api service (skip dev group)
# We need the full sources of workspace members for editable install
COPY packages/ packages/
COPY services/api/ services/api/

# Sync just the api service's deps (and its transitive workspace deps)
RUN uv sync --no-dev --package agentradar-api --frozen

# ---------- Runtime stage --------------------------------------------------
FROM python:3.12-slim AS runtime

# Non-root user for least-privilege execution
RUN groupadd --system --gid 1000 agentradar \
    && useradd --system --uid 1000 --gid agentradar --no-create-home agentradar

WORKDIR /app

# Copy the prebuilt venv and source from the builder
COPY --from=builder --chown=agentradar:agentradar /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

USER agentradar

# Use uvicorn directly so signals and reload behavior are clean
CMD ["uvicorn", "agentradar_api.main:app", "--host", "0.0.0.0", "--port", "8000"]