# syntax=docker/dockerfile:1.7
# ─────────────────────────────────────────────────────────────────────────────
# mcp-filesystem (fs_basic) — production container. Generated from MCP_Math's
# templates/Dockerfile.multi.template pattern (see MCP_Math/templates/README.md) —
# a true uv workspace, so one root `uv sync` covers the single fs_basic member.
#
# Build:  docker build -t mcp-filesystem-fs-basic:latest .
# Run:    docker run --rm -p 8801:8801 -v $PWD/fs-root:/data mcp-filesystem-fs-basic:latest
# Auth:   docker run --rm -p 8801:8801 -e FS_API_KEY=secret mcp-filesystem-fs-basic:latest
#
# fs_basic operates on whatever path its tool calls are given — bind-mount
# the directory tree you want it to manage (e.g. at /data) and pass paths
# under that mount in your tool calls.
# ─────────────────────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.14-slim

FROM python:${PYTHON_VERSION} AS builder
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
COPY pyproject.toml uv.lock README.md ./
COPY shared ./shared
COPY servers ./servers
RUN uv sync --frozen

FROM python:${PYTHON_VERSION} AS runtime
RUN groupadd -r app && useradd -r -g app app \
    && mkdir -p /home/app && chown app:app /home/app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/shared /app/shared
COPY --from=builder /app/servers /app/servers
COPY pyproject.toml ./

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    FS_TRANSPORT=http \
    FS_HOST=0.0.0.0 \
    FS_PORT=8801

USER app
EXPOSE 8801

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"FS_PORT\"]}/health', timeout=3)" || exit 1

ENTRYPOINT ["python", "/app/servers/fs_basic/server.py"]
