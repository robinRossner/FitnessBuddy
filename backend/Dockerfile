# FitnessBuddy backend. Multi-arch base (runs on the Oracle Cloud Ampere A1 / arm64 VM).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN useradd --uid 1000 --create-home app && mkdir -p /app/var && chown -R app /app/var
USER app

ENV DATABASE_URL=sqlite:////app/var/fitness.db
VOLUME ["/app/var"]
EXPOSE 8000

# migrate, then serve (single worker: SQLite)
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
