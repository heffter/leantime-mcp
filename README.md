<!--
SPDX-FileCopyrightText: 2025 Daniel Eder

SPDX-License-Identifier: CC0-1.0
-->

# Leantime MCP Server

A Model Context Protocol (MCP) server that provides AI assistants with access to Leantime's (leantime.io) JsonRPC 2.0 API. This enables AI tools like Claude to interact with Leantime projects, tickets, timesheets, users, and more through a standardized interface.

This server uses [FastMCP](https://github.com/jlowin/fastmcp) which supports multiple transport protocols including stdio, HTTP, WebSocket, and SSE, making it suitable for various deployment scenarios.

The leantime mcp plugin is not needed.
If you own the leantime mcp plugin consider using https://github.com/Leantime/php-mcp-server instead.

## About this fork

This is a hardened fork of [daniel-eder/leantime-mcp](https://github.com/daniel-eder/leantime-mcp) intended for self-hosted deployment as a long-running container on a home LAN. Pre-deployment changes applied (see `AUDIT.md` for the full audit that motivated them):

- **Dependencies pinned** to bounded version ranges and the lockfile is the source of truth for installs (`uv sync --frozen`).
- **Dead code removed** (`tools.py` was an orphan parallel schema definition).
- **Type fixes** in `create_ticket` so `status` and `assignedTo` are `int` matching `update_ticket` and the output of `get_status_labels`.
- **Configurable logging** via the `LOG_LEVEL` env var.
- **`/health` liveness endpoint** for Docker `HEALTHCHECK` and reverse proxies.
- **`get_user_by_email` tool** wired up (the underlying client method already existed).
- **Dockerfile, `.dockerignore`, `docker-compose.yml`** added for a non-root, read-only, capability-dropped container.

## MCP Client Configuration

This project uses [uv](https://github.com/astral-sh/uv) for fast, reliable Python package management. Ensure it is installed before modifying your MCP settings.

To use with Claude Desktop or other MCP clients, add to your MCP settings:

### STDIO Transport (Default)

For local MCP clients like Claude Desktop that communicate via standard input/output:

```json
{
  "mcpServers": {
    "leantime": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/daniel-eder/leantime-mcp.git",
        "leantime-mcp"
      ],
      "env": {
        "LEANTIME_URL": "https://your-leantime-instance.com",
        "LEANTIME_API_KEY": "your_api_key_here",
        "LEANTIME_USER_EMAIL": "your_email@example.com"
      }
    }
  }
}
```

### HTTP Transport

For remote HTTP connections, first start the server with HTTP transport (see [Running the Server](#running-the-server)), then configure your MCP client to connect to the HTTP endpoint:

```json
{
  "mcpServers": {
    "leantime-http": {
      "url": "http://localhost:8000/mcp",
      "transport": "streamableHttp"
    }
  }
}
```

**Note:** The HTTP transport configuration depends on your MCP client's support for HTTP connections. The server must be running separately using the `fastmcp run` command with `--transport http` option. Make sure to set the required environment variables (`LEANTIME_URL`, `LEANTIME_API_KEY`, `LEANTIME_USER_EMAIL`) when starting the HTTP server.

## Getting a Leantime API Key

1. Log in to your Leantime instance
2. Go to Company -> API Keys 
3. Generate a new API key

## Running the Server

This server supports multiple transport protocols for different deployment scenarios:

### STDIO Transport (Default)

For use with MCP clients like Claude Desktop that communicate via standard input/output:

```bash
# Using the entry point
leantime-mcp

# Or run directly
python -m src.leantime_mcp.server
```

### HTTP Transport

For remote access via HTTP, useful for web services and remote clients:

```bash
# Run on default port 8000
fastmcp run src/leantime_mcp/server.py:app --transport http

# Run on custom port
fastmcp run src/leantime_mcp/server.py:app --transport http --port 9000

# When developing (without installing the package), use uv run:
uv run fastmcp run src/leantime_mcp/server.py:app --transport http
```

Once running, the MCP endpoint will be available at `http://localhost:8000/mcp` (or your custom network address/port).

### SSE Transport (Legacy)

Server-Sent Events transport for legacy web applications:

```bash
fastmcp run src/leantime_mcp/server.py:app --transport sse --port 8000
```

### Using FastMCP CLI

The FastMCP CLI provides additional options and better control:

```bash
# See all available options
fastmcp run --help

# Run with debug logging
fastmcp run src/leantime_mcp/server.py:app --transport http --log-level DEBUG
```

### Environment Variables

Set these environment variables for all transport types:

```bash
export LEANTIME_URL="https://your-leantime-instance.com"
export LEANTIME_API_KEY="your_api_key_here"
export LEANTIME_USER_EMAIL="your_email@example.com"
export LOG_LEVEL="INFO"   # optional; one of DEBUG/INFO/WARNING/ERROR/CRITICAL
```

`LOG_LEVEL` defaults to `INFO`. Invalid values fall back to `INFO` with a warning. Note that at `DEBUG`, full RPC parameter payloads (ticket bodies, comment text, timesheet descriptions) are logged — fine for local debugging on a trusted host, not appropriate for shipping logs off-box.

### Health endpoint

When running on the HTTP transport, the server exposes:

```
GET /health  -> 200 {"status": "ok"}
```

This is a pure liveness probe. It does **not** contact Leantime, so it stays green even if Leantime is unreachable — it only confirms the MCP process itself is alive. Used by the Docker `HEALTHCHECK` in the included `Dockerfile`.

## Available Tools

The server provides the following MCP tools:

- `get_project` - Get details of a specific project
- `list_projects` - List all accessible projects
- `create_project` - Create a new project
- `get_ticket` - Get ticket/task details
- `list_tickets` - List tickets (optionally filtered by project)
- `create_ticket` - Create a new ticket
- `update_ticket` - Update an existing ticket
- `get_user` - Get user details
- `get_user_by_email` - Look up a user by email address
- `list_users` - List all users
- `add_comment` - Add a comment to a ticket or project
- `get_comments` - Get comments for a module
- `add_timesheet` - Log time to a ticket
- `get_timesheets` - Query timesheet entries


## Deployment

The recommended way to run this fork as a long-running service is the included `docker-compose.yml`. The image is multi-stage, runs as a non-root user, is mounted read-only, drops all Linux capabilities, and sets `no-new-privileges`. The HEALTHCHECK probes `/health`.

Intended hostname for the deployed service: `leantime.mcp.home.lan`. Container name: `leantime-mcp` (Docker disallows dots in container names).

### Quickstart with docker-compose

```bash
# 1. Configure secrets
cp .env.example .env
$EDITOR .env       # set LEANTIME_URL, LEANTIME_API_KEY, LEANTIME_USER_EMAIL, LOG_LEVEL

# 2. Build and start
docker compose up -d --build

# 3. Verify
curl http://leantime.mcp.home.lan:8000/health
# -> {"status":"ok"}

# 4. Logs
docker compose logs -f leantime-mcp
```

The MCP endpoint is then available at `http://leantime.mcp.home.lan:8000/mcp` for clients that support `streamableHttp`.

### Reverse proxy and authentication

**This codebase has no MCP-layer authentication.** Anyone who can reach port 8000 has full read/write to your Leantime data through the configured API key. Put a reverse proxy with auth in front of it. A minimal Caddyfile example:

```caddy
leantime.mcp.home.lan {
    basic_auth /mcp* {
        # generate with: caddy hash-password
        someuser <hashed-password>
    }
    reverse_proxy localhost:8000
}
```

Other reasonable options: Tailscale / WireGuard so only authenticated devices can reach the host at all; Traefik or nginx with basic auth or mTLS.

### Pinning and supply chain

This fork pins direct dependencies to bounded version ranges in `pyproject.toml` and commits `uv.lock`. The Dockerfile installs with `uv sync --frozen` so the lockfile is the source of truth at build time.

**Do not use `uvx --from git+https://...` for production deployment** — that path ignores the lockfile and resolves fresh against PyPI on every install, defeating the pinning. Build the Docker image and run from there.

## Development

### Setup Development Environment

```bash
# Clone the repository
git clone https://github.com/daniel-eder/leantime-mcp.git
cd leantime-mcp

# Sync dependencies (includes dev dependencies)
uv sync

# Run from source
uv run leantime-mcp
```

## Links

- [Leantime](https://leantime.io/)
- [Leantime API Documentation](https://docs.leantime.io/api/README)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [MCP Specification](https://spec.modelcontextprotocol.io/)

## Licensing

This project uses [REUSE](https://reuse.software/) for clear and comprehensive licensing information, following the [FSFE REUSE specification](https://reuse.software/spec/).

### License Information

All files contain SPDX license headers for clear identification. To check compliance:

```bash
uvx reuse lint
```