# SPDX-FileCopyrightText: 2025 Daniel Eder
#
# SPDX-License-Identifier: MIT

"""Leantime JSON-RPC 2.0 client implementation."""

import asyncio
import logging
import os
import random
import time
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Tunable via env vars so deployments can override without a code change.
#
# Reactive layer (handles 429s that slip through the proactive limiter or
# come from a tighter shared limit, e.g. several MCP clients on one
# Leantime instance):
#   LEANTIME_MAX_RETRIES = number of retry attempts on a 429 (default 5).
#   LEANTIME_BACKOFF_BASE = base seconds for exponential schedule (default
#     1.0; n-th retry waits ~base * 2^n with 25% jitter, capped at CAP).
#   LEANTIME_BACKOFF_CAP = absolute max sleep between retries (default 30s).
#
# Proactive layer (token-bucket pacing, defaults match Leantime's stock
# LEAN_RATELIMIT_API = 10 req per 60s window):
#   LEANTIME_RATE_LIMIT_PER_MIN = sustained req/min target (default 10;
#     0 disables proactive pacing entirely, leaving only the reactive
#     retry layer).
#   LEANTIME_RATE_BURST = max burst capacity in tokens (default 10; lets
#     short bursts pass at full speed after a quiet period).
_MAX_RETRIES = int(os.getenv("LEANTIME_MAX_RETRIES", "5"))
_BACKOFF_BASE = float(os.getenv("LEANTIME_BACKOFF_BASE", "1.0"))
_BACKOFF_CAP = float(os.getenv("LEANTIME_BACKOFF_CAP", "30.0"))
_RATE_LIMIT_PER_MIN = float(os.getenv("LEANTIME_RATE_LIMIT_PER_MIN", "10"))
_RATE_BURST = float(os.getenv("LEANTIME_RATE_BURST", "10"))
# Per-request timeout in seconds (raised from the previous hard-coded 30s).
# Some Leantime queries (e.g. getAllMilestones with progress join across
# many milestones) can take >30s on a busy host. Tunable to give callers
# room without re-rolling the image.
_TIMEOUT = float(os.getenv("LEANTIME_TIMEOUT", "60.0"))
# Number of additional attempts on a ReadTimeout for idempotent reads.
# Writes (create/update/delete/...) are NEVER retried on timeout because
# the server may have already applied the operation -- retrying could
# duplicate the action.
_TIMEOUT_RETRIES = int(os.getenv("LEANTIME_TIMEOUT_RETRIES", "1"))

# Method-name prefixes considered safe to retry on timeout. The check
# applies to the last dotted segment of the JSON-RPC method string,
# lowercased: e.g. `leantime.rpc.Tickets.Tickets.getAllMilestones`
# -> `getallmilestones` -> starts with `get` -> idempotent.
_IDEMPOTENT_PREFIXES = (
    "get", "list", "find", "is", "has", "poll", "read",
)


def _is_idempotent_method(method: str) -> bool:
    """Return True if the JSON-RPC method is safe to retry on timeout."""
    last = method.rsplit(".", 1)[-1].lower()
    return any(last.startswith(p) for p in _IDEMPOTENT_PREFIXES)


class _AsyncTokenBucket:
    """Async token-bucket rate limiter for a single Leantime endpoint.

    Refills at `rate_per_sec` tokens per second up to `capacity`. Each
    acquire() removes one token, sleeping inside the lock if the bucket
    is empty so callers serialize FIFO when the limit is saturated.

    rate_per_sec <= 0 disables the limiter; acquire() returns immediately.
    """

    def __init__(self, rate_per_sec: float, capacity: float):
        self.rate = max(0.0, rate_per_sec)
        self.capacity = max(1.0, capacity)
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.rate > 0.0

    async def acquire(self) -> None:
        if not self.enabled:
            return
        async with self._lock:
            now = time.monotonic()
            self._tokens = min(
                self.capacity,
                self._tokens + (now - self._last) * self.rate,
            )
            self._last = now
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            wait = (1.0 - self._tokens) / self.rate
            logger.info(
                "Leantime rate-limit pacing: sleeping %.2fs before next call "
                "(target %.1f req/min)",
                wait, self.rate * 60,
            )
            await asyncio.sleep(wait)
            self._tokens = 0.0
            self._last = time.monotonic()


class LeantimeAPIError(Exception):
    """Exception raised for Leantime API errors."""
    
    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"Leantime API Error {code}: {message}")


class LeantimeClient:
    """Client for interacting with Leantime's JSON-RPC 2.0 API."""
    
    def __init__(self, base_url: str, api_key: str):
        """Initialize the Leantime client.

        Args:
            base_url: Base URL of the Leantime instance (e.g., https://leantime.example.com)
            api_key: API key for authentication
        """
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.endpoint = f"{self.base_url}/api/jsonrpc"
        self._request_id = 0
        # Proactive pacing layer; keeps us under Leantime's per-minute window
        # so the reactive retry-on-429 in call() rarely has to fire.
        self._limiter = _AsyncTokenBucket(
            rate_per_sec=_RATE_LIMIT_PER_MIN / 60.0,
            capacity=_RATE_BURST,
        )
    
    def _get_next_id(self) -> int:
        """Get next JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id
    
    async def call(self, method: str, params: Optional[dict] = None) -> Any:
        """Make a JSON-RPC 2.0 call to Leantime API.

        Retries automatically on 429 (Leantime's per-minute rate limit)
        with backoff before giving up. Honors a `Retry-After` response
        header if Leantime sends one; otherwise falls back to exponential
        backoff (~1s, 2s, 4s, 8s, 16s) with 25% jitter, capped at
        LEANTIME_BACKOFF_CAP seconds per retry. Tuneable via env vars
        (see module-level constants).

        Args:
            method: RPC method name (e.g., "leantime.rpc.Projects.getProject")
            params: Method parameters as dictionary

        Returns:
            The result from the JSON-RPC response

        Raises:
            LeantimeAPIError: If the API returns an error
            httpx.HTTPStatusError: If a non-429 HTTP error occurred, or 429
                persisted past LEANTIME_MAX_RETRIES attempts.
            httpx.HTTPError: If there's a network/HTTP error
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": self._get_next_id()
        }

        headers = {
            "Content-Type": "application/json",
            "X-API-KEY": self.api_key
        }

        logger.debug(f"Calling Leantime RPC: {method} with params: {params}")

        # Proactive pacing: wait for a token before issuing the request, so
        # we stay under Leantime's per-minute limit. The retry loop below is
        # the reactive safety net for the residual cases (concurrent clients
        # sharing the limit, drift between our config and Leantime's, etc.).
        await self._limiter.acquire()

        idempotent = _is_idempotent_method(method)
        timeout_attempts_left = _TIMEOUT_RETRIES if idempotent else 0

        async with httpx.AsyncClient() as client:
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    response = await client.post(
                        self.endpoint,
                        json=payload,
                        headers=headers,
                        timeout=_TIMEOUT,
                    )
                except httpx.ReadTimeout as exc:
                    # Idempotent reads: retry once (or LEANTIME_TIMEOUT_RETRIES
                    # times). Writes and other non-idempotent calls fail fast --
                    # the server may have already applied the change, retrying
                    # could duplicate it.
                    if timeout_attempts_left > 0:
                        timeout_attempts_left -= 1
                        logger.warning(
                            "Leantime ReadTimeout on idempotent %s after %.0fs; "
                            "retrying (%d attempt(s) left)",
                            method, _TIMEOUT, timeout_attempts_left + 1,
                        )
                        continue
                    raise LeantimeAPIError(
                        code=-32099,
                        message=(
                            f"Timeout calling {method} after {_TIMEOUT:.0f}s"
                            + (" (no retry: not an idempotent operation)"
                               if not idempotent else
                               " (retries exhausted)")
                        ),
                        data=str(exc),
                    ) from exc

                if response.status_code == 429 and attempt < _MAX_RETRIES:
                    delay = self._compute_backoff(response, attempt)
                    logger.warning(
                        "Leantime rate-limited (429) on %s "
                        "(attempt %d/%d); backing off for %.1fs",
                        method, attempt + 1, _MAX_RETRIES + 1, delay,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                data = response.json()

                # Check for JSON-RPC error
                if "error" in data:
                    error = data["error"]
                    raise LeantimeAPIError(
                        code=error.get("code", -1),
                        message=error.get("message", "Unknown error"),
                        data=error.get("data")
                    )

                return data.get("result")

            # Retries exhausted; surface the last 429 to the caller.
            response.raise_for_status()

    @staticmethod
    def _compute_backoff(response: httpx.Response, attempt: int) -> float:
        """Pick a sleep duration for the next retry.

        Honors `Retry-After` (seconds form; HTTP-date form is rare for
        per-minute rate limiters and falls through to exponential backoff
        if unparseable). Otherwise: base * 2^attempt with 25% jitter,
        capped at _BACKOFF_CAP.
        """
        retry_after = response.headers.get("Retry-After", "").strip()
        if retry_after:
            try:
                return min(max(float(retry_after), 0.0), _BACKOFF_CAP)
            except ValueError:
                pass
        delay = _BACKOFF_BASE * (2 ** attempt)
        delay += random.uniform(0, delay * 0.25)
        return min(delay, _BACKOFF_CAP)
    
    # Convenience methods for common operations
    
    async def get_project(self, project_id: int) -> dict:
        """Get project details by ID."""
        return await self.call("leantime.rpc.Projects.getProject", {"id": project_id})
    
    async def list_projects(self) -> list:
        """List all projects."""
        return await self.call("leantime.rpc.Projects.getAll")
    
    async def create_project(self, name: str, details: Optional[str] = None, **kwargs) -> dict:
        """Create a new project."""
        params = {"name": name, **kwargs}
        if details:
            params["details"] = details
        return await self.call("leantime.rpc.Projects.addProject", params)
    
    async def edit_project(self, project_id: int, values: dict) -> Any:
        """Update an existing project's metadata, preserving unspecified fields.

        Leantime's editProject repository unconditionally overwrites every
        column (name, details, clientId, state, hourBudget, dollarBudget,
        psettings, menuType, type, parent, start, end), defaulting absent
        keys to '' or null. To prevent silent data loss this method
        fetches the current project first and merges the caller's
        `values` over the current state.

        `values` is a dict of fields to change; common keys are name,
        details, clientId, state, type, hourBudget, dollarBudget. Pass
        only what you want to change.
        """
        current = await self.get_project(project_id)
        if not current:
            raise ValueError(f"Project with ID {project_id} not found")
        merged: dict[str, Any] = {
            "name": current.get("name", ""),
            "details": current.get("details", ""),
            "clientId": current.get("clientId", ""),
            "state": current.get("state", ""),
            "hourBudget": current.get("hourBudget", ""),
            "dollarBudget": current.get("dollarBudget", ""),
            "psettings": current.get("psettings", ""),
            "menuType": current.get("menuType", "default"),
            "type": current.get("type", "project"),
            "parent": current.get("parent"),
            "start": current.get("start"),
            "end": current.get("end"),
        }
        for key, value in values.items():
            if value is not None:
                merged[key] = value
        return await self.call(
            "leantime.rpc.Projects.editProject",
            {"id": project_id, "values": merged},
        )

    async def patch_project(self, project_id: int, params: dict) -> bool:
        """Partial-update a project: only fields present in params are written."""
        return await self.call(
            "leantime.rpc.Projects.patch",
            {"id": project_id, "params": params},
        )

    async def duplicate_project(self, project_id: int, client_id: int,
                                project_name: str, user_start_date: Optional[str] = None,
                                assign_same_users: bool = True) -> Any:
        """Deep-clone a project (tickets, milestones, canvases) into a new project."""
        params: dict = {
            "projectId": project_id,
            "clientId": client_id,
            "projectName": project_name,
            "assignSameUsers": assign_same_users,
        }
        if user_start_date is not None:
            params["userStartDate"] = user_start_date
        return await self.call("leantime.rpc.Projects.duplicateProject", params)

    async def get_project_progress(self, project_id: int) -> Any:
        """Return overall progress data for a project."""
        return await self.call("leantime.rpc.Projects.getProjectProgress", {"projectId": project_id})

    async def get_users_assigned_to_project(self, project_id: int,
                                            team_only: bool = False) -> list:
        """List users assigned to a project. team_only=True excludes clients."""
        return await self.call(
            "leantime.rpc.Projects.getUsersAssignedToProject",
            {"projectId": project_id, "teamOnly": team_only},
        )

    async def edit_user_project_relations(self, user_id: int, project_ids: list) -> bool:
        """Replace the full set of projects a user is assigned to."""
        return await self.call(
            "leantime.rpc.Projects.editUserProjectRelations",
            {"id": user_id, "projects": project_ids},
        )

    async def get_ticket(self, ticket_id: int) -> dict:
        """Get ticket details by ID."""
        return await self.call("leantime.rpc.Tickets.Tickets.getTicket", {"id": ticket_id})
    
    async def list_tickets(self, project_id: Optional[int] = None) -> list:
        """List tickets, optionally filtered by project."""
        searchCriteria = {}
        if project_id:
            searchCriteria["currentProject"] = project_id
        params = {"searchCriteria": searchCriteria}
        return await self.call("leantime.rpc.Tickets.Tickets.getAll", params)
    
    async def create_ticket(self, headline: str, project_id: int, user_id: int,
                            date: Optional[str] = None, tags: Optional[str] = None,
                            milestone_id: Optional[int] = None,
                            sprint_id: Optional[int] = None,
                            **kwargs) -> dict:
        """Create a new ticket.

        Args:
            headline: Title/headline of the ticket
            project_id: Project ID where the ticket will be created
            user_id: The ID of the user creating the ticket
            date: The date when the ticket is created (YYYY-MM-DD format). Defaults to current date if not provided.
            tags: Comma-separated list of tags to add to the ticket
            milestone_id: Optional milestone (ticket of type=milestone) to assign this ticket to.
            sprint_id: Optional sprint to assign this ticket to.
            **kwargs: Additional parameters. `assignedTo` is translated to
                Leantime's wire field name `editorId` (Leantime's
                addTicket reads `$values['editorId']`, NOT `assignedTo`,
                so the previous version silently dropped the assignment).
        """
        from datetime import datetime

        # Use current date if none provided
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        # Translate the Pythonic camelCase name to Leantime's wire field.
        # Without this rename, `assignedTo=N` lands in `values['assignedTo']`
        # which Leantime's addTicket service silently ignores; the assignee
        # then falls back to the empty default and the ticket appears
        # unassigned in the UI. assignedTo=0 maps to the empty-string
        # "unassign" sentinel (consistent with milestone_id=0 / sprint_id=0).
        if "assignedTo" in kwargs:
            _val = kwargs.pop("assignedTo")
            if _val is not None:
                kwargs["editorId"] = "" if _val == 0 else _val

        # The API expects a 'values' parameter containing the ticket data
        values = {
            "headline": headline,
            "projectId": project_id,
            "userId": user_id,
            "date": date,
            **kwargs
        }

        # Add tags if provided
        if tags is not None:
            values["tags"] = tags
        if milestone_id is not None:
            values["milestoneid"] = milestone_id
        if sprint_id is not None:
            values["sprint"] = sprint_id

        params = {"values": values}
        return await self.call("leantime.rpc.Tickets.Tickets.addTicket", params)
    
    async def update_ticket(self, ticket_id: int, project_id: Optional[int] = None,
                            milestone_id: Optional[int] = None,
                            sprint_id: Optional[int] = None,
                            **kwargs) -> dict:
        """Update an existing ticket, preserving any field the caller doesn't change.

        Leantime's updateTicket service is a partial-write hazard: it
        reconstructs every field as `$values['X'] ?? ''` and the
        repository unconditionally overwrites every column. Calling it
        with only `{"id": tid, "headline": "x"}` blanks status, type,
        description, milestoneid, sprint, tags, priority, dependingTicketId,
        editFrom, editTo, etc. To prevent that, this method fetches the
        current ticket first and merges the caller's changes over the
        full current state.

        Args:
            ticket_id: The ID of the ticket to update.
            project_id: Optional new projectId. If None, the current
                projectId is preserved.
            milestone_id: Optional milestone link. Pass an int to attach,
                pass 0 to detach, pass None to leave unchanged.
            sprint_id: Optional sprint link. Same semantics as milestone_id.
            **kwargs: Other ticket fields to update (headline, description,
                status, priority, etc.). None values are ignored
                (current value preserved).
        """
        current = await self.get_ticket(ticket_id)
        if not current:
            raise ValueError(f"Ticket with ID {ticket_id} not found")

        # Start from the ticket's current state, then layer changes on top.
        values: dict[str, Any] = {
            "id": ticket_id,
            "headline": current.get("headline", ""),
            "type": current.get("type", "task"),
            "description": current.get("description", ""),
            "projectId": current.get("projectId"),
            "editorId": current.get("editorId", ""),
            "dateToFinish": current.get("dateToFinish", ""),
            "status": current.get("status", ""),
            "planHours": current.get("planHours", ""),
            "tags": current.get("tags", ""),
            "sprint": current.get("sprint", ""),
            "storypoints": current.get("storypoints", ""),
            "hourRemaining": current.get("hourRemaining", ""),
            "priority": current.get("priority", ""),
            "acceptanceCriteria": current.get("acceptanceCriteria", ""),
            "editFrom": current.get("editFrom", ""),
            "editTo": current.get("editTo", ""),
            "dependingTicketId": current.get("dependingTicketId", ""),
            "milestoneid": current.get("milestoneid", ""),
        }

        if project_id is not None:
            values["projectId"] = project_id
        # milestone_id and sprint_id use 0 as the "detach" sentinel so callers
        # can clear the link explicitly without confusing it with "leave alone".
        if milestone_id is not None:
            values["milestoneid"] = milestone_id if milestone_id else ""
        if sprint_id is not None:
            values["sprint"] = sprint_id if sprint_id else ""

        # Translate the Pythonic name to Leantime's wire field BEFORE the
        # merge loop. Otherwise we'd send both the merged-in current
        # editorId AND the caller's intended new value under the wrong key
        # (assignedTo) -- Leantime would persist the current value and
        # silently ignore the requested change. assignedTo=0 maps to the
        # empty-string "unassign" sentinel (matches milestone_id=0 /
        # sprint_id=0 detach semantics).
        if "assignedTo" in kwargs:
            _val = kwargs.pop("assignedTo")
            if _val is not None:
                kwargs["editorId"] = "" if _val == 0 else _val

        # Apply the rest of the caller's changes; None means "preserve current".
        for key, value in kwargs.items():
            if value is not None:
                values[key] = value

        return await self.call("leantime.rpc.Tickets.Tickets.updateTicket", {"values": values})
    
    async def delete_ticket(self, ticket_id: int) -> Any:
        """Delete a ticket by ID.

        Note: the underlying RPC method is `delete` (not `deleteTicket`).
        Subtasks are tickets too, so this also handles subtask removal.
        """
        return await self.call("leantime.rpc.Tickets.Tickets.delete", {"id": ticket_id})

    async def patch_ticket(self, ticket_id: int, params: dict) -> bool:
        """Partial-update a ticket: only fields present in params are written.

        Strips framework-internal keys server-side. Useful for narrow edits
        like just changing the status without resending the full ticket.
        """
        return await self.call(
            "leantime.rpc.Tickets.Tickets.patch",
            {"id": ticket_id, "params": params},
        )

    async def quick_create_ticket(self, headline: str, project_id: int, editor_id: int,
                                  description: Optional[str] = None,
                                  ticket_type: str = "task",
                                  status: Optional[int] = None,
                                  storypoints: Optional[int] = None,
                                  plan_hours: Optional[int] = None,
                                  sprint: Optional[int] = None,
                                  priority: Optional[int] = None,
                                  date_to_finish: Optional[str] = None) -> Any:
        """Lightweight ticket creation with a reduced field set.

        Like the quick* milestone helpers, quickAddTicket wraps its fields
        under an inner 'params' key (Leantime contract quirk).
        """
        inner: dict = {
            "headline": headline,
            "type": ticket_type,
            "projectId": project_id,
            "editorId": editor_id,
        }
        if description is not None:
            inner["description"] = description
        if status is not None:
            inner["status"] = status
        if storypoints is not None:
            inner["storypoints"] = storypoints
        if plan_hours is not None:
            inner["planHours"] = plan_hours
        if sprint is not None:
            inner["sprint"] = sprint
        if priority is not None:
            inner["priority"] = priority
        if date_to_finish is not None:
            inner["dateToFinish"] = date_to_finish
        return await self.call("leantime.rpc.Tickets.Tickets.quickAddTicket", {"params": inner})

    async def move_ticket(self, ticket_id: int, project_id: int) -> bool:
        """Move a ticket (and milestone children if applicable) to a different project."""
        return await self.call(
            "leantime.rpc.Tickets.Tickets.moveTicket",
            {"id": ticket_id, "projectId": project_id},
        )

    async def get_status_labels(self) -> dict:
        """Get all available ticket status labels with their IDs.
        
        Returns:
            A dictionary mapping status IDs to their labels
        """
        return await self.call("leantime.rpc.Tickets.Tickets.getStatusLabels")
    
    async def get_user(self, user_id: int) -> dict:
        """Get user details by ID."""
        return await self.call("leantime.rpc.Users.getUser", {"id": user_id})
    
    async def list_users(self) -> list:
        """List all users."""
        return await self.call("leantime.rpc.Users.getAll")
    
    async def get_user_by_email(self, email: str) -> dict:
        """Get user details by email address."""
        return await self.call("leantime.rpc.Users.Users.getUserByEmail", {"email": email})
    
    async def create_user(self, firstname: str, lastname: str, username: str,
                          password: str, role: str = "20",
                          phone: Optional[str] = None,
                          client_id: Optional[int] = None,
                          status: str = "a",
                          job_title: Optional[str] = None,
                          job_level: Optional[str] = None,
                          department: Optional[str] = None) -> Any:
        """Create a new user account. Admin-only at runtime.

        role is a Leantime role string ('10' developer, '20' editor,
        '30' commenter, '40' admin, '50' owner). status is 'a' active or
        'i' inactive. username is the email address used to log in.

        Leantime's addUser expects fields under a 'values' key (matching
        editUser's contract); flat params return -32602 'Required Parameter
        Missing: values'.
        """
        values: dict = {
            "firstname": firstname,
            "lastname": lastname,
            "username": username,
            "password": password,
            "role": role,
            "status": status,
        }
        if phone is not None:
            values["phone"] = phone
        if client_id is not None:
            values["clientId"] = client_id
        if job_title is not None:
            values["jobTitle"] = job_title
        if job_level is not None:
            values["jobLevel"] = job_level
        if department is not None:
            values["department"] = department
        return await self.call("leantime.rpc.Users.addUser", {"values": values})

    async def update_user(self, user_id: int, values: dict) -> bool:
        """Update an existing user's profile fields. Admin-only at runtime.

        Leantime's editUser repository code reads `firstname`, `lastname`,
        `user`, `status`, `role`, and `clientId` from the values dict
        without an isset() guard, so omitting any of them produces an
        "Undefined array key" server error. It also uses the awkward key
        `user` (not `username`) for the email address.

        To preserve a "pass only the fields you want to change" UX, this
        method fetches the current user record first and merges the
        caller's values over the current state, including the username
        field renamed to `user` for the editUser contract.
        """
        current = await self.get_user(user_id)
        if not current:
            raise ValueError(f"User with ID {user_id} not found")
        merged: dict[str, Any] = {
            "firstname": values.get("firstname", current.get("firstname", "")),
            "lastname": values.get("lastname", current.get("lastname", "")),
            "user": values.get("user", values.get("username", current.get("username", ""))),
            "status": values.get("status", current.get("status", "a")),
            "role": values.get("role", current.get("role", "20")),
            "clientId": values.get("clientId", current.get("clientId") or 0),
        }
        for opt in ("phone", "jobTitle", "jobLevel", "department",
                    "hours", "wage", "password"):
            if opt in values:
                merged[opt] = values[opt]
            elif current.get(opt) is not None:
                merged[opt] = current[opt]
        return await self.call(
            "leantime.rpc.Users.editUser",
            {"id": user_id, "values": merged},
        )

    async def delete_user(self, user_id: int) -> bool:
        """Delete a user and remove all project relations. Admin-only at runtime."""
        return await self.call("leantime.rpc.Users.deleteUser", {"id": user_id})

    # Comments

    async def add_comment(self, module: str, entity_id: int, text: str,
                          father: Optional[int] = None,
                          entity_headline: Optional[str] = None) -> Any:
        """Add a comment to a Leantime entity (typically a ticket or project).

        Uses the Leantime 3.x API shape (entityId + values:{text}).
        Internally, this method synthesises Leantime's expected `entity`
        wire-level argument from `module`, `entity_id`, and the optional
        `entity_headline` (used by the server to compose notification
        email subjects).

        IMPORTANT: Leantime's addComment validates `isset($values['father'])`
        - the key must be present even for top-level (non-reply) comments.
        We always include it, defaulting to 0 when no parent is specified.
        Returning [false] from the upstream method usually means this key
        was missing or the comment text was empty after validation.
        """
        values: dict = {"text": text, "father": father if father is not None else 0}
        entity = {"type": module, "id": entity_id}
        if entity_headline is not None:
            entity["headline"] = entity_headline
        params = {
            "values": values,
            "module": module,
            "entityId": entity_id,
            "entity": entity,
        }
        return await self.call("leantime.rpc.Comments.addComment", params)

    async def update_comment(self, comment_id: int, text: str) -> bool:
        """Update an existing comment's text."""
        return await self.call(
            "leantime.rpc.Comments.editComment",
            {"id": comment_id, "values": {"text": text}},
        )

    async def delete_comment(self, comment_id: int) -> bool:
        """Delete a comment by ID."""
        return await self.call("leantime.rpc.Comments.deleteComment", {"commentId": comment_id})

    async def get_comments(self, module: str, entity_id: int) -> list:
        """Get comments for a module entity (ticket / project / etc.).

        The Leantime 3.x API uses `entityId` (was `moduleId` on older
        versions); current Leantime rejects `moduleId` with -32602.
        """
        params = {
            "module": module,
            "entityId": entity_id,
        }
        return await self.call("leantime.rpc.Comments.getComments", params)
    
    async def add_timesheet(self, user_id: int, ticket_id: int, hours: float, date: str, **kwargs) -> dict:
        """Add a timesheet entry."""
        params = {
            "userId": user_id,
            "ticketId": ticket_id,
            "hours": hours,
            "date": date,
            **kwargs
        }
        return await self.call("leantime.rpc.Timesheets.addTime", params)

    async def upsert_timesheet(self, ticket_id: int, user_id: int, date: str,
                               hours: float, kind: str = "GENERAL_BILLABLE",
                               description: Optional[str] = None) -> Any:
        """Create or update a time entry for a ticket on a specific date.

        Replaces the older addTime / logTime pattern. kind is one of the
        Leantime time kinds (default 'GENERAL_BILLABLE').
        """
        inner: dict = {
            "userId": user_id,
            "date": date,
            "kind": kind,
            "hours": hours,
        }
        if description is not None:
            inner["description"] = description
        return await self.call(
            "leantime.rpc.Timesheets.upsertTime",
            {"ticketId": ticket_id, "params": inner},
        )

    async def delete_timesheet(self, timesheet_id: int) -> Any:
        """Delete a time entry by ID."""
        return await self.call("leantime.rpc.Timesheets.deleteTime", {"id": timesheet_id})
    
    async def get_timesheets(self, project_id: Optional[int] = None,
                             user_id: Optional[int] = None) -> list:
        """List recent timesheet entries (poll-style).

        Leantime 3.x does NOT expose a generic "list all timesheets"
        RPC method - the underlying `getAll` and `getWeeklyTimesheets`
        accept Carbon date objects that JSON-RPC cannot construct. The
        only callable list endpoints are the change-detection polls
        `pollForNewTimesheets` and `pollForUpdatedTimesheets`. This
        method calls the former, which returns recently-created entries.

        For full enumeration, use the Leantime web UI's Timesheets view.
        """
        params: dict = {}
        if project_id is not None:
            params["projectId"] = project_id
        if user_id is not None:
            params["userId"] = user_id
        return await self.call("leantime.rpc.Timesheets.pollForNewTimesheets", params)

    async def poll_updated_timesheets(self, project_id: Optional[int] = None,
                                      user_id: Optional[int] = None) -> list:
        """Poll for recently-modified timesheet entries."""
        params: dict = {}
        if project_id is not None:
            params["projectId"] = project_id
        if user_id is not None:
            params["userId"] = user_id
        return await self.call("leantime.rpc.Timesheets.pollForUpdatedTimesheets", params)
    
    async def get_all_subtasks(self, ticket_id: int) -> list:
        """Get all subtasks for a ticket.
        
        Args:
            ticket_id: The ID of the parent ticket
            
        Returns:
            A list of subtasks or false if an error occurred
        """
        params = {"ticketId": ticket_id}
        return await self.call("leantime.rpc.Tickets.Tickets.getAllSubtasks", params)
    
    async def list_milestones(self, project_id: int, sort_by: str = "standard") -> list:
        """List all milestones for a project.

        Milestones in Leantime are tickets with type=milestone, so this hits
        the Tickets module's dedicated milestone endpoint.
        """
        params = {
            "searchCriteria": {"currentProject": project_id},
            "sortBy": sort_by,
        }
        return await self.call("leantime.rpc.Tickets.Tickets.getAllMilestones", params)

    async def create_milestone(self, headline: str, project_id: int, editor_id: int,
                               edit_from: Optional[str] = None, edit_to: Optional[str] = None,
                               tags: Optional[str] = None,
                               dependent_milestone: Optional[int] = None) -> int:
        """Create a milestone (server-side type=milestone). Returns the new milestone ID.

        editor_id is the Leantime user ID owning the milestone (typically the
        creator). edit_from / edit_to are the milestone start/end dates in
        YYYY-MM-DD format and are passed through verbatim.

        quickAddMilestone wraps its fields under an inner 'params' key (a
        Leantime contract quirk; verified against the running instance).
        """
        inner: dict = {
            "headline": headline,
            "projectId": project_id,
            "editorId": editor_id,
        }
        if edit_from is not None:
            inner["editFrom"] = edit_from
        if edit_to is not None:
            inner["editTo"] = edit_to
        if tags is not None:
            inner["tags"] = tags
        if dependent_milestone is not None:
            inner["dependentMilestone"] = dependent_milestone
        return await self.call("leantime.rpc.Tickets.Tickets.quickAddMilestone", {"params": inner})

    async def update_milestone(self, milestone_id: int, editor_id: int,
                               headline: Optional[str] = None,
                               edit_from: Optional[str] = None,
                               edit_to: Optional[str] = None,
                               status: Optional[int] = None,
                               tags: Optional[str] = None) -> dict:
        """Update a milestone's lightweight fields.

        Leantime's quickUpdateMilestone fails with "Undefined array key" on
        any field the PHP method touches but the request omits, so this
        method fetches the milestone first and merges user-supplied fields
        over the current values. Pass only the fields you want to change.

        editor_id is required by Leantime for activity attribution.
        """
        current = await self.get_ticket(milestone_id)
        if not current:
            raise ValueError(f"Milestone with ID {milestone_id} not found")
        # Status fallback uses an explicit None check instead of `or 3`
        # because Leantime status IDs are integers and could include 0;
        # the truthy form would silently rewrite a stored 0 to 3.
        if status is not None:
            resolved_status = status
        else:
            cur_status = current.get("status")
            resolved_status = int(cur_status) if cur_status is not None else 3
        inner: dict = {
            "id": milestone_id,
            "editorId": editor_id,
            "headline": headline if headline is not None else current.get("headline", ""),
            "editFrom": edit_from if edit_from is not None else (current.get("editFrom") or ""),
            "editTo": edit_to if edit_to is not None else (current.get("editTo") or ""),
            "tags": tags if tags is not None else (current.get("tags") or ""),
            "status": resolved_status,
            "dependentMilestone": current.get("dependentMilestone") or "",
        }
        return await self.call("leantime.rpc.Tickets.Tickets.quickUpdateMilestone", {"params": inner})

    async def delete_milestone(self, milestone_id: int) -> dict:
        """Delete a milestone by its ticket ID."""
        return await self.call("leantime.rpc.Tickets.Tickets.deleteMilestone", {"id": milestone_id})

    # Note: leantime.rpc.Tickets.Tickets.getMilestoneProgress is not exposed
    # as an MCP tool — its PHP signature expects a Milestone model object,
    # not an int, so the JSON-RPC dispatcher cannot cast a plain integer.
    # Verified empirically against the running instance.

    # Sprints

    async def list_sprints(self, project_id: int) -> list:
        """List all sprints for a project."""
        return await self.call("leantime.rpc.Sprints.Sprints.getAllSprints", {"projectId": project_id})

    async def get_sprint(self, sprint_id: int) -> dict:
        """Fetch a single sprint by ID."""
        return await self.call("leantime.rpc.Sprints.Sprints.getSprint", {"id": sprint_id})

    async def get_current_sprint_id(self, project_id: int) -> Any:
        """Return the ID of the currently active sprint for a project, or false."""
        return await self.call("leantime.rpc.Sprints.Sprints.getCurrentSprintId", {"projectId": project_id})

    async def list_future_sprints(self, project_id: int) -> Any:
        """List all future (not yet started) sprints for a project."""
        return await self.call("leantime.rpc.Sprints.Sprints.getAllFutureSprints", {"projectId": project_id})

    async def get_upcoming_sprint(self, project_id: int) -> Any:
        """Return the next scheduled sprint for a project."""
        return await self.call("leantime.rpc.Sprints.Sprints.getUpcomingSprint", {"projectId": project_id})

    async def get_sprint_cumulative_report(self, project_id: int) -> Any:
        """Return cumulative-flow report data for a project's sprints."""
        return await self.call("leantime.rpc.Sprints.Sprints.getCummulativeReport", {"project": project_id})

    async def create_sprint(self, name: str, project_id: int, start_date: str, end_date: str) -> Any:
        """Create a new sprint. Returns the new sprint ID or false."""
        params = {
            "name": name,
            "projectId": project_id,
            "startDate": start_date,
            "endDate": end_date,
        }
        return await self.call("leantime.rpc.Sprints.Sprints.addSprint", params)

    async def update_sprint(self, sprint_id: int,
                            name: Optional[str] = None,
                            project_id: Optional[int] = None,
                            start_date: Optional[str] = None,
                            end_date: Optional[str] = None) -> Any:
        """Update an existing sprint (name / project / dates).

        Leantime's editSprint repository unconditionally writes name,
        projectId, startDate and endDate from a fresh Sprints model
        instance, so unspecified fields would be persisted as null. Fetch
        the current sprint and layer the caller's changes over it.
        """
        current = await self.get_sprint(sprint_id)
        if not current:
            raise ValueError(f"Sprint with ID {sprint_id} not found")
        params: dict[str, Any] = {
            "id": sprint_id,
            "name": current.get("name", ""),
            "projectId": current.get("projectId"),
            "startDate": current.get("startDate", ""),
            "endDate": current.get("endDate", ""),
        }
        if name is not None:
            params["name"] = name
        if project_id is not None:
            params["projectId"] = project_id
        if start_date is not None:
            params["startDate"] = start_date
        if end_date is not None:
            params["endDate"] = end_date
        return await self.call("leantime.rpc.Sprints.Sprints.editSprint", params)

    # Goalcanvas

    async def list_goals(self, project_id: Optional[int] = None,
                         board_id: Optional[int] = None) -> list:
        """List goals, optionally filtered by project and/or board."""
        params: dict = {}
        if project_id is not None:
            params["projectId"] = project_id
        if board_id is not None:
            params["board"] = board_id
        return await self.call("leantime.rpc.Goalcanvas.Goalcanvas.pollGoals", params)

    async def list_goal_board_items(self, board_id: int) -> list:
        """List all goal items on a specific Goalcanvas board, with progress."""
        return await self.call("leantime.rpc.Goalcanvas.Goalcanvas.getCanvasItemsById", {"id": board_id})

    async def create_goal(self, values: dict) -> int:
        """Create a goal item. `values` is a free-form Leantime goal dict;
        common keys: title, description, projectId, board, currentValue,
        startValue, endValue, metricType, parent.
        """
        return await self.call("leantime.rpc.Goalcanvas.Goalcanvas.createGoal", {"values": values})

    # Files (attachments) - read + delete only.
    # Upload requires a PHP $_FILES-style array which JSON-RPC cannot carry;
    # use Leantime's web UI or HTTP multipart endpoint to upload files.

    async def list_files_for_module(self, module: str, entity_id: int,
                                    user_id: Optional[int] = None) -> list:
        """List files attached to a module entity (e.g. module='ticket', entity_id=<ticket id>)."""
        params: dict = {"module": module, "entityId": entity_id}
        if user_id is not None:
            params["userId"] = user_id
        return await self.call("leantime.rpc.Files.Files.getFilesByModule", params)

    async def delete_file(self, file_id: int) -> bool:
        """Delete a file attachment by its file ID."""
        return await self.call("leantime.rpc.Files.Files.deleteFile", {"fileId": file_id})

    # Wiki - read only.
    # Create / update endpoints in Leantime's Wiki service expect PHP model
    # objects rather than plain dicts, so they cannot be invoked from a
    # standard JSON-RPC client. Use the web UI to author wiki content.

    async def list_wikis(self, project_id: int) -> list:
        """List wiki spaces in a project."""
        return await self.call("leantime.rpc.Wiki.Wiki.getAllProjectWikis", {"projectId": project_id})

    async def get_wiki(self, wiki_id: int) -> dict:
        """Fetch a single wiki space's metadata."""
        return await self.call("leantime.rpc.Wiki.Wiki.getWiki", {"id": wiki_id})

    async def list_wiki_articles(self, wiki_id: int, user_id: int) -> list:
        """List article headlines in a wiki space."""
        params = {"wikiId": wiki_id, "userId": user_id}
        return await self.call("leantime.rpc.Wiki.Wiki.getAllWikiHeadlines", params)

    async def get_wiki_article(self, article_id: int,
                               project_id: Optional[int] = None) -> dict:
        """Fetch a wiki article (full content)."""
        params: dict = {"id": article_id}
        if project_id is not None:
            params["projectId"] = project_id
        return await self.call("leantime.rpc.Wiki.Wiki.getArticle", params)

    async def get_wiki_article_history(self, article_id: int,
                                       limit: int = 20) -> list:
        """Fetch a wiki article's revision/edit history."""
        return await self.call("leantime.rpc.Wiki.Wiki.getArticleActivity",
                               {"articleId": article_id, "limit": limit})

    # Ideas - read-only polling.
    # The Leantime Ideas service exposes only poll endpoints over RPC;
    # CRUD methods for ideas are not @api-callable. Use the web UI to
    # create / edit ideas.

    async def list_new_ideas(self, project_id: Optional[int] = None,
                             board_id: Optional[int] = None) -> list:
        """Poll for newly created ideas, optionally scoped to project and board."""
        params: dict = {}
        if project_id is not None:
            params["projectId"] = project_id
        if board_id is not None:
            params["board"] = board_id
        return await self.call("leantime.rpc.Ideas.Ideas.pollForNewIdeas", params)

    async def list_updated_ideas(self, project_id: Optional[int] = None,
                                 board_id: Optional[int] = None) -> list:
        """Poll for recently modified ideas, optionally scoped to project and board."""
        params: dict = {}
        if project_id is not None:
            params["projectId"] = project_id
        if board_id is not None:
            params["board"] = board_id
        return await self.call("leantime.rpc.Ideas.Ideas.pollForUpdatedIdeas", params)

    async def upsert_subtask(self, parent_ticket_id: int, headline: str, date: Optional[str] = None, tags: Optional[str] = None, **kwargs) -> dict:
        """Create or update a subtask.
        
        Args:
            parent_ticket_id: The ID of the parent ticket
            headline: Title/headline of the subtask
            date: The date when the subtask is created (YYYY-MM-DD format). Defaults to current date if not provided.
            tags: Comma-separated list of tags to add to the subtask
            **kwargs: Additional parameters (description, status, priority, assignedTo, etc.)
            
        Returns:
            The created subtask data
        """
        from datetime import datetime
        
        # Use current date if none provided
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")
        
        # Fetch the parent ticket data to get project_id and milestone_id
        parent_ticket_data = await self.get_ticket(parent_ticket_id)
        
        if not parent_ticket_data:
            raise ValueError(f"Parent ticket with ID {parent_ticket_id} not found")
        
        # Extract required fields from parent ticket
        project_id = parent_ticket_data.get("projectId")
        if not project_id:
            raise ValueError(f"Could not determine projectId from parent ticket {parent_ticket_id}")
        
              # Extract required fields from parent ticket
        user_id = parent_ticket_data.get("userId")
        if not user_id:
            raise ValueError(f"Could not determine userId from parent ticket {parent_ticket_id}")

        milestone_id = parent_ticket_data.get("milestoneid")

        # Translate the Pythonic camelCase name to Leantime's wire field.
        # upsert_subtask routes through addTicket internally, and addTicket
        # reads $values['editorId'], NOT $values['assignedTo']; without this
        # rename the caller's assignedTo lands in the values dict but
        # Leantime silently drops it, leaving the subtask unassigned.
        # Mirrors the same translation in create_ticket / update_ticket.
        # assignedTo=0 maps to the empty-string "unassign" sentinel.
        if "assignedTo" in kwargs:
            _val = kwargs.pop("assignedTo")
            if _val is not None:
                kwargs["editorId"] = "" if _val == 0 else _val

        # The API expects a 'values' parameter containing the subtask data
        values = {
            "headline": headline,
            "type": "subtask",  # Mark this as a subtask
            "projectId": project_id,
            "userId": user_id,
            "date": date,
            "dependingTicketId": parent_ticket_id,  # Link to parent ticket
            "milestoneid": milestone_id if milestone_id else "",  # Use parent's milestone
            **kwargs
        }
        
        # Add tags if provided
        if tags is not None:
            values["tags"] = tags
        
        # Use addTicket to create the subtask
        params = {"values": values}
        
        # Debug logging
        logger.info(f"Creating subtask via addTicket: type=subtask, dependingTicketId={parent_ticket_id}, milestoneid={milestone_id}")
        
        return await self.call("leantime.rpc.Tickets.Tickets.addTicket", params)
