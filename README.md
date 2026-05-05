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

The server exposes 60 MCP tools spanning the Leantime domains below, plus two non-tool HTTP routes (`GET /health` and `GET /version` — see [Health endpoint](#health-endpoint) and [Versioning](#versioning-and-is-this-the-latest-binary-check)). Each tool's docstring is the canonical reference; this list is the index.

**Diagnostics**

- `get_version` - return package version, git commit, build date, and runtime versions for "is the right binary deployed?" checks

**Projects**

- `get_project` / `list_projects` / `create_project`
- `edit_project(project_id, values)` / `patch_project(project_id, params)` (partial update)
- `duplicate_project(project_id, client_id, project_name, ...)` - deep clone (copies tickets, milestones, canvases; not comments / files / timesheets)
- `get_project_progress` / `get_users_assigned_to_project` / `edit_user_project_relations`
- *Not exposed*: `delete_project` - doesn't exist in Leantime's service layer; use the web UI.

**Tickets**

- `get_ticket` / `list_tickets`
- `create_ticket` / `update_ticket` - accept optional `milestone_id` and `sprint_id` to attach the ticket to a milestone or sprint
- `quick_create_ticket(...)` - reduced field set, wraps fields under inner `{"params": {...}}` per Leantime's contract
- `patch_ticket(ticket_id, params)` - partial update; only fields present in `params` are written
- `delete_ticket(ticket_id)` - hard delete (subtasks too)
- `move_ticket(ticket_id, project_id)` - move ticket and milestone children to a different project
- `get_status_labels` - resolve status names to IDs

**Subtasks**

- `get_all_subtasks(ticket_id)`
- `upsert_subtask(parent_ticket, headline, ...)` - inherits the parent's project and milestone

**Users**

- `get_user` / `get_user_by_email` / `list_users`
- `create_user(firstname, lastname, username, password, role='20', ...)` - admin scoped
- `update_user(user_id, values)` / `delete_user(user_id)` - admin scoped

**Comments**

- `add_comment(module, entity_id, text, father?, entity_headline?)` - works on any Leantime module (ticket, project, etc.)
- `update_comment(comment_id, text)` / `delete_comment(comment_id)`
- `get_comments(module, entity_id)`

**Timesheets**

- `add_timesheet(...)` (legacy `addTime` path) and `upsert_timesheet(ticket_id, user_id, date, hours, kind?, description?)` - prefer upsert
- `delete_timesheet(timesheet_id)`
- `get_timesheets(project_id?, user_id?)` - poll-style, returns recently-created entries (Leantime has no generic "list all timesheets" RPC; the underlying `getAll` requires Carbon objects that JSON-RPC cannot carry)
- `poll_updated_timesheets(project_id?, user_id?)` - sibling for recently-modified entries

**Milestones**

- `list_milestones(project_id)` / `create_milestone(...)` / `update_milestone(...)` / `delete_milestone(milestone_id)`
- Use `create_ticket(..., milestone_id=<id>)` to assign a new ticket to a milestone, or `update_ticket(..., milestone_id=<id>)` for an existing one (`milestone_id=0` to detach).
- `get_milestone_progress` is *not* exposed: Leantime's RPC method requires a Milestone PHP object that JSON-RPC cannot construct.

**Sprints**

- `list_sprints` / `get_sprint` / `get_current_sprint_id` / `list_future_sprints` / `get_upcoming_sprint`
- `get_sprint_cumulative_report(project_id)` - cumulative-flow report data
- `create_sprint(name, project_id, start_date, end_date)` / `update_sprint(sprint_id, ...)` - no `delete_sprint`; Leantime does not expose deletion via RPC
- Use `create_ticket(..., sprint_id=<id>)` / `update_ticket(..., sprint_id=<id>)` for ticket-sprint membership.

**Goals (Goalcanvas)**

- `list_goals(project_id?, board_id?)` / `list_goal_board_items(board_id)`
- `create_goal(title, project_id, board_id, description?, current_value?, start_value?, end_value?, metric_type?, assigned_to?, parent?)`
- *Not exposed*: `update_goal` / `delete_goal` - Leantime's Goalcanvas service does not expose update or delete methods over RPC. Edit goals in the web UI.

**Files (read-only)**

- `list_files_for_module(module, entity_id)` / `delete_file(file_id)`
- File **upload** is not exposed via MCP. Leantime's upload endpoint requires a multipart/form-data POST that JSON-RPC cannot carry; use the Leantime web UI to attach files.

**Wiki (read-only)**

- `list_wikis(project_id)` / `get_wiki(wiki_id)`
- `list_wiki_articles(wiki_id, user_id)` / `get_wiki_article(article_id, project_id?)` / `get_wiki_article_history(article_id, limit=20)`
- Wiki article **creation/editing** is not exposed via MCP because Leantime's `createArticle` / `updateArticle` accept PHP model objects, not JSON. Use the web UI to author wiki content.

**Ideas (read-only polling)**

- `list_new_ideas(project_id?, board_id?)` / `list_updated_ideas(project_id?, board_id?)`
- The Leantime Ideas service does not expose CRUD endpoints over RPC; idea creation/editing is web-UI only.

### Known Leantime API quirks the tools work around

These are documented inline in each tool's docstring; collected here as a quick reference.

- **`add_comment` returns `-32000` even on success** when authenticated via API key. Leantime's `addComment` saves the comment, then dispatches a notification using `session('userdata.id')`. In stateless API contexts that session value is null, the notification step throws, and the error bubbles up. The comment IS persisted - verify with `get_comments`. (Source: `app/Domain/Comments/Services/Comments.php`.)
- **`add_comment` requires `father` to always be present** in the values dict. Leantime checks `isset($values['father'])`, which is false in PHP for missing keys; the tool always sends it (defaulting to 0). Older versions accepted the key being absent.
- **`update_milestone` fetches before writing.** Leantime's `quickUpdateMilestone` fails with "Undefined array key" on any field the PHP method touches but the request omits, so the tool reads the current milestone first and merges your changes over it. Pass only the fields you want to change.
- **`update_user` fetches before writing, and renames `username` to `user`.** Leantime's `editUser` repository code reads `firstname`/`lastname`/`user`/`status`/`role`/`clientId` without an isset() guard and uses the awkward key `user` (not `username`) for the email address. The tool reads the current user record first, merges your changes, and translates the field name automatically. Pass only the fields you want to change.
- **`Users.addUser` / `Users.editUser` wrap fields under `{"values": {...}}`** rather than accepting flat top-level params. Handled internally by `create_user` / `update_user`; you don't need to think about it.
- **`assignedTo` is renamed to `editorId` on the wire** for `create_ticket` and `update_ticket`. Leantime's `addTicket` and `updateTicket` services read `$values['editorId']`, NOT `$values['assignedTo']`, so the previous version silently dropped the assignee. The Pythonic `assignedTo` parameter on the MCP tool is preserved (no migration cost for callers); the translation happens in the client. `assignedTo=0` maps to the empty-string "unassign" sentinel, matching the `milestone_id=0` / `sprint_id=0` detach convention.
- **Leantime's API rate limit is `LEAN_RATELIMIT_API` requests per 60-second window** (default `10`). To stay under it the MCP client uses two layers, both tunable on the container without rebuilding:
  - **Proactive pacing** — token-bucket limiter that consumes one token per outbound RPC call, refilling at the configured per-minute rate. Burst capacity lets short flurries pass at full speed after a quiet period. Defaults match Leantime's stock config (`LEANTIME_RATE_LIMIT_PER_MIN=10`, `LEANTIME_RATE_BURST=10`); raise both if you've raised `LEAN_RATELIMIT_API` on your Leantime instance. Set `LEANTIME_RATE_LIMIT_PER_MIN=0` to disable pacing entirely.
  - **Reactive retry-on-429** — if the proactive layer's pace doesn't match Leantime's reality (e.g. a second client shares the limit, or your Leantime config drifted), 429s trigger automatic retry with exponential backoff (`~1s, 2s, 4s, 8s, 16s`) and 25% jitter, honoring a `Retry-After` response header if Leantime sends one. Tunables: `LEANTIME_MAX_RETRIES` (default 5), `LEANTIME_BACKOFF_BASE` (default 1.0s), `LEANTIME_BACKOFF_CAP` (default 30s). Persistent 429s past the retry budget surface as `httpx.HTTPStatusError`.

  In normal use you won't see 429s in the logs anymore; instead you might see one `INFO` line per pacing event (`Leantime rate-limit pacing: sleeping 0.42s before next call`) when the bucket is empty. That's the proactive layer doing its job.
- **`get_milestone_progress` is not exposed.** The PHP signature requires a `Milestone` model object; JSON-RPC cannot construct it.
- **`getTimesheets`/`getAll` for timesheets is not exposed.** Same Carbon-object issue. `get_timesheets` here uses `pollForNewTimesheets` instead.
- **No deletion** for projects, sprints, or goals via RPC - Leantime's service layer does not expose those operations. Use the web UI.
- **`quick*` methods wrap their fields under an inner `{"params": {...}}` key** rather than `{"values": {...}}` like normal ticket mutations. Handled internally; you don't need to think about it from the MCP side.


## Deployment

The recommended way to run this fork as a long-running service is the included `docker-compose.yml`. The image is multi-stage, runs as a non-root user, is mounted read-only, drops all Linux capabilities, and sets `no-new-privileges`. The HEALTHCHECK probes `/health`.

Intended hostname for the deployed service: `leantime.mcp.home.lan`. Container name: `leantime-mcp` (Docker disallows dots in container names).

### Choosing `LEANTIME_URL`

There are two recommended shapes depending on how your hosts are laid out:

| Topology | `LEANTIME_URL` | Why |
|---|---|---|
| **Co-located** — MCP and Leantime on the same host | `http://<host-internal-ip>:<leantime-port>` (e.g. `http://10.0.0.110:8090`) | Traffic never leaves the box, so DNS, reverse proxy, and TLS termination are pure overhead. Fewer dependencies, fewer failure modes. |
| **Cross-host** — MCP and Leantime on different machines | `https://leantime.example.com` | External name resolution and TLS certificate verification both matter once traffic crosses the network. Use the public DNS name. |

The container only ever calls `LEANTIME_URL`; everything else (the MCP endpoint your clients connect to) is independent of this setting. You can safely change just this one variable to switch topologies.

### Quickstart with docker-compose

```bash
# 1. Configure secrets
cp .env.example .env
$EDITOR .env       # set LEANTIME_URL, LEANTIME_API_KEY, LEANTIME_USER_EMAIL, LOG_LEVEL

# 2. Build and start (use the wrapper so version info gets baked in)
./scripts/build-and-deploy.sh

# 3. Verify liveness and identity (through the reverse proxy with auth)
curl -k -u user:pass https://leantime.mcp.home.lan/health
# -> {"status":"ok"}
curl -k -u user:pass https://leantime.mcp.home.lan/version
# -> {"package_version":"1.0.1","git_commit":"...","build_date":"...",...}

# 4. Logs
docker compose logs -f leantime-mcp
```

The MCP endpoint is then available at `https://leantime.mcp.home.lan/mcp` (proxied — no host port binding; the container is only reachable through the reverse proxy on the shared `leantime_mcp_net` Docker network) for clients that support `streamableHttp`.

### Versioning and "is this the latest binary?" check

The image bakes in three pieces of identity at build time: the package version (`1.0.0`, bumped manually on meaningful releases — see `pyproject.toml`), the git commit it was built from, and a UTC build timestamp. They are exposed three ways:

- The `get_version` MCP tool — call it from any MCP client to get a JSON dict with `package_version`, `git_commit`, `git_commit_short`, `git_commit_date`, `build_date`, plus the runtime `python_version` / `fastmcp_version` / `mcp_version` / `platform`.
- The `GET /version` HTTP endpoint — same payload over plain HTTP, useful for external monitoring or scripting without an MCP client.
- Standard OCI image labels (`org.opencontainers.image.revision`, `.created`, `.source`, `.title`) — visible via `docker inspect leantime-mcp:local --format '{{json .Config.Labels}}'`.

To check whether your deployed container is on the commit you expect:

```bash
# Container's view
curl -fsSk -u user:pass https://leantime.mcp.home.lan/version | jq -r .git_commit
# Local checkout's view (run on the host where you built the image)
git rev-parse HEAD
```

Match -> deployed. Mismatch -> rebuild with `./scripts/build-and-deploy.sh` (which forwards `GIT_COMMIT`/`GIT_COMMIT_DATE`/`BUILD_DATE` to the build via compose args). A plain `docker compose build` without the wrapper produces an image that reports `git_commit: "unknown"` because the build context can't introspect git on its own — the wrapper's only job is to set those env vars before invoking compose.

### Troubleshooting: LAN-local hostnames fail to resolve inside the container

If `LEANTIME_URL` uses a hostname in a LAN-local TLD (`.home.lan`, `.local`, `.lan`, `.internal`, an mDNS name, or anything that's only resolvable via your router or `/etc/hosts`), DNS will fail from inside the container. Docker's default bridge network does **not** inherit the host's `/etc/hosts` entries or use mDNS, so the container's resolver cannot see those names even though your shell on the host can. Symptom: tool calls fail with `httpx.ConnectError`/`getaddrinfo failed` even though `curl <url>` works fine on the host.

Three fixes, easiest first:

1. **Use the IP directly** — set `LEANTIME_URL=http://<lan-ip>:<port>`. This is the co-located case in the table above and side-steps DNS entirely.
2. **Pin the hostname with `--add-host`** — add `--add-host=leantime.home.lan:10.0.0.110` to `docker run`, or `extra_hosts: ["leantime.home.lan:10.0.0.110"]` to your compose service. The container gets a static `/etc/hosts` entry just for that name.
3. **Point Docker at your LAN DNS** — `--dns=<your-LAN-DNS-IP>` on `docker run`, or `dns: [<ip>]` in compose. Cleanest if you have an internal DNS server that authoritatively serves the zone.

To verify resolution from inside the container:

```bash
docker exec leantime-mcp getent hosts leantime.home.lan
docker exec leantime-mcp wget --spider -S http://10.0.0.110:8090/
```

### Reverse proxy and authentication

**This codebase has no MCP-layer authentication.** The compose file uses `expose` rather than `ports`, so the container has no host port binding — the only path in is through a reverse proxy attached to the same `leantime_mcp_net` Docker network. The proxy is therefore where authentication lives.

A minimal Caddy example (Caddy container must also be on `leantime_mcp_net` and target the MCP container by its service name, not `localhost`):

```caddy
leantime.mcp.home.lan {
    basic_auth /mcp* {
        # generate with: caddy hash-password
        someuser <hashed-password>
    }
    reverse_proxy leantime-mcp:8000
}
```

```yaml
# docker-compose.caddy.yml (or merge into a single compose file)
services:
  caddy:
    image: caddy:2
    networks:
      - leantime_mcp_net
    ports:
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
networks:
  leantime_mcp_net:
    external: true                # created by leantime-mcp's compose
volumes:
  caddy_data:
```

Bearer-token / mTLS / OAuth are equally valid replacements for the `basic_auth` directive — pick whichever your other tooling already speaks. Other reasonable options: Tailscale / WireGuard so only authenticated devices can reach the proxy host at all.

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