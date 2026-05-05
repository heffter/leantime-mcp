#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Daniel Eder
#
# SPDX-License-Identifier: MIT
#
# Build and (re)deploy the leantime-mcp container with build-time identity
# baked in, so `get_version` inside the running server can report the exact
# commit it was built from. Run from the repo root.

set -euo pipefail

cd "$(dirname "$0")/.."

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "Not in a git checkout - GIT_COMMIT will fall back to 'unknown'." >&2
fi

GIT_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo unknown)
GIT_COMMIT_DATE=$(git show -s --format=%cI HEAD 2>/dev/null || echo unknown)
BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

export GIT_COMMIT GIT_COMMIT_DATE BUILD_DATE

echo "Building leantime-mcp:"
echo "  git commit: ${GIT_COMMIT}"
echo "  commit date: ${GIT_COMMIT_DATE}"
echo "  build date:  ${BUILD_DATE}"

docker compose build --no-cache
docker compose up -d --force-recreate

echo "Deployed. The container is on the leantime_mcp_net Docker network"
echo "with no host port binding, so verify through the reverse proxy:"
echo "  curl -fsSk -u user:pass https://leantime.mcp.home.lan/version"
echo "Inside-container probe (no auth, on the same Docker host):"
echo "  docker exec leantime-mcp wget -qO- http://127.0.0.1:8000/version"
