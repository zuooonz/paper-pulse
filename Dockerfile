FROM python:3.12-slim AS builder

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON=/opt/venv/bin/python \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /build

# Install uv outside the project environment so `uv sync` cannot prune it.
RUN python -m pip install --no-cache-dir uv==0.13.6
RUN python -m venv "$VIRTUAL_ENV"

# Dependency layer: keyed only by pyproject.toml + uv.lock.  Editing src/
# below no longer invalidates the (slow) dependency installation.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra postgres --no-install-project

COPY src/ ./src/
RUN uv sync --frozen --no-dev --extra postgres --no-editable

FROM python:3.12-slim AS runtime

ARG INSTALL_PLAYWRIGHT=false
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV JOURNAL_METRICS_DB=/app/reference/jcr.db

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY src/ ./src/
COPY config/ ./config/
COPY data/reference/ /app/reference/
COPY scripts/ ./scripts/
COPY migrations/ ./migrations/
COPY alembic.ini ./alembic.ini

RUN if [ "$INSTALL_PLAYWRIGHT" = "true" ]; then \
      playwright install --with-deps chromium && \
      mkdir -p /ms-playwright && chown -R 10001:10001 /ms-playwright; \
    fi \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/data /app/output \
    && chown -R app:app /app \
    && chmod -R a-w /app/reference

VOLUME ["/app/data", "/app/output"]
EXPOSE 8000
USER app

CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS:-1} --timeout-keep-alive ${UVICORN_KEEP_ALIVE:-75}"]
