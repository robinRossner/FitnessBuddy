# FitnessBuddy backend. Multi-arch (linux/amd64 + linux/arm64: Oracle Ampere A1 VM).

# ---- builder: resolve + install locked deps into /opt/venv with uv ----
FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ---- runtime: no uv, no build tooling, non-root ----
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/tmp \
    DATABASE_URL=sqlite:////app/var/fitness.db

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY . .

# All mutable state lives in /app/var (DB, var/fit, var/garminconnect, var/garmin_token.json);
# BACKEND_DIR resolves to /app, so the settings defaults already point there.
# compose may run the container as another uid (the host user) so the bind mount stays writable
# for both the app and the host-side backup cron.
RUN useradd --uid 1000 --no-create-home app \
    && mkdir -p /app/var/fit /app/var/garminconnect \
    && chown -R app /app/var \
    && chmod +x /app/docker-entrypoint.sh
USER app

VOLUME ["/app/var"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
