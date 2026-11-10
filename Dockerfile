# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.7.21

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src

# Keep the package readme optional in the Docker build context.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,target=/build-context \
    if [ -f /build-context/README.md ]; then \
        cp /build-context/README.md README.md; \
    else \
        touch README.md; \
    fi && \
    uv sync --frozen --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PASTEBIN_STORAGE_DIR=/data \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --gid 10001 pastebin \
    && useradd --uid 10001 --gid pastebin --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin pastebin \
    && mkdir -p /data \
    && chown pastebin:pastebin /data

COPY --from=builder --chown=pastebin:pastebin /app/.venv /app/.venv

USER pastebin

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"]

CMD ["uvicorn", "pastebin.main:app", "--host", "0.0.0.0", "--port", "8000"]
