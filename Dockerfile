# SPDX-FileCopyrightText: 2025 Daniel Eder
#
# SPDX-License-Identifier: MIT
#
# Multi-stage build:
#   builder  - resolves and installs deps from the committed lockfile
#   runtime  - non-root, no build tools, only the venv + source
# syntax=docker/dockerfile:1.7

# ---- builder ----
FROM python:3.11-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install --no-cache-dir "uv==0.4.*"

WORKDIR /app
# README.md is required: pyproject.toml declares `readme = "README.md"` and
# hatchling validates the file exists when building the wheel.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Frozen install: lockfile is authoritative; no resolver drift on rebuild.
# --no-dev: drops dev deps. --no-editable: copy the package, don't symlink.
RUN uv sync --frozen --no-dev --no-editable

# ---- runtime ----
FROM python:3.11-slim AS runtime

# wget for HEALTHCHECK; smaller install footprint than curl on slim.
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*

# Non-root system user with no login shell.
RUN groupadd --system app \
    && useradd --system --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_LEVEL=INFO

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://127.0.0.1:8000/health || exit 1

CMD ["fastmcp", "run", "src/leantime_mcp/server.py:app", "--transport", "http", "--host", "0.0.0.0", "--port", "8000"]
