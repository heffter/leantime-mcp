# SPDX-FileCopyrightText: 2025 Daniel Eder
#
# SPDX-License-Identifier: MIT

"""Build-identity information for the running MCP server.

Three pieces of information come from three different places:

* `package_version` is hard-coded in `__init__.py` (and `pyproject.toml`),
  bumped manually on meaningful releases.
* `git_commit` / `git_commit_date` are baked in at Docker build time via
  `ARG GIT_COMMIT` / `ARG GIT_COMMIT_DATE`. When running outside Docker
  (e.g. `uv run leantime-mcp` from a checkout) we fall back to reading
  the local `.git` directory directly.
* `build_date` is set by the Dockerfile via `ARG BUILD_DATE` at the time
  of `docker build`; in a non-Docker run we report process start time.
"""

import os
import platform
import subprocess
from datetime import datetime, timezone
from typing import Any

from leantime_mcp import __version__ as _PACKAGE_VERSION


_PROCESS_START = datetime.now(timezone.utc).isoformat(timespec="seconds")


def _from_env_or_git(env_key: str, git_args: list[str]) -> str:
    """Return the value baked in via env, else read the local .git, else 'unknown'."""
    val = os.getenv(env_key, "").strip()
    if val:
        return val
    try:
        out = subprocess.check_output(
            ["git", *git_args],
            stderr=subprocess.DEVNULL,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            timeout=2,
        )
        return out.decode().strip() or "unknown"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return "unknown"


def get_build_info() -> dict[str, Any]:
    """Return a structured snapshot of the running build's identity.

    The shape is stable so MCP clients can compare fields between calls
    (e.g. "did my redeploy actually update the git_commit?").
    """
    git_commit = _from_env_or_git("GIT_COMMIT", ["rev-parse", "HEAD"])
    git_commit_short = git_commit[:7] if git_commit and git_commit != "unknown" else "unknown"
    git_commit_date = _from_env_or_git("GIT_COMMIT_DATE", ["show", "-s", "--format=%cI", "HEAD"])
    build_date = os.getenv("BUILD_DATE", "").strip() or _PROCESS_START

    # Lazily import so a missing package never crashes get_build_info.
    def _pkg(name: str) -> str:
        try:
            from importlib.metadata import version as _v
            return _v(name)
        except Exception:
            return "unknown"

    return {
        "package_version": _PACKAGE_VERSION,
        "git_commit": git_commit,
        "git_commit_short": git_commit_short,
        "git_commit_date": git_commit_date,
        "build_date": build_date,
        "python_version": platform.python_version(),
        "fastmcp_version": _pkg("fastmcp"),
        "mcp_version": _pkg("mcp"),
        "platform": f"{platform.system()} {platform.release()}",
    }
