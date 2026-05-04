# SPDX-FileCopyrightText: 2025 Daniel Eder
#
# SPDX-License-Identifier: MIT

"""Leantime MCP Server - Main server implementation."""

import os
import sys
import json
import logging
from typing import Optional
from dotenv import load_dotenv

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

from leantime_mcp.client import LeantimeClient, LeantimeAPIError

logger = logging.getLogger(__name__)

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def _configure_logging() -> None:
    """Configure root logging from the LOG_LEVEL env var (default INFO).

    Invalid values fall back to INFO with a warning. Invoked at module
    import time so the level applies under both the `leantime-mcp` console
    script (which calls main()) and `fastmcp run server.py:app` (which
    imports the module and runs `app` directly without calling main()).
    Subsequent calls are idempotent: logging.basicConfig is documented as
    a no-op once a handler is attached to the root logger.
    """
    requested = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if requested in _VALID_LOG_LEVELS:
        level = requested
    else:
        logging.basicConfig(level=logging.INFO)
        logger.warning(
            "Invalid LOG_LEVEL %r; expected one of %s. Falling back to INFO.",
            requested, sorted(_VALID_LOG_LEVELS),
        )
        return
    logging.basicConfig(level=getattr(logging, level))


# Load environment variables and configure logging before FastMCP/uvicorn
# initialise their own loggers, so LOG_LEVEL takes effect everywhere.
load_dotenv()
_configure_logging()

# Initialize the FastMCP server
app = FastMCP("leantime-mcp")

# Global Leantime client instance
leantime_client: LeantimeClient = None


def get_client() -> LeantimeClient:
    """Get or create the Leantime client instance."""
    global leantime_client
    
    if leantime_client is None:
        # Get configuration from environment
        leantime_url = os.getenv("LEANTIME_URL")
        leantime_api_key = os.getenv("LEANTIME_API_KEY")
        leantime_user_email = os.getenv("LEANTIME_USER_EMAIL")
        
        if not leantime_url:
            raise ValueError(
                "LEANTIME_URL environment variable is required. "
                "Please set it in your .env file or environment."
            )
        
        if not leantime_api_key:
            raise ValueError(
                "LEANTIME_API_KEY environment variable is required. "
                "Please set it in your .env file or environment."
            )
        
        if not leantime_user_email:
            raise ValueError(
                "LEANTIME_USER_EMAIL environment variable is required. "
                "Please set it in your .env file or environment."
            )
        
        leantime_client = LeantimeClient(leantime_url, leantime_api_key)
        logger.info(f"Initialized Leantime client for {leantime_url}")
    
    return leantime_client


# Tool functions will be defined below


@app.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_request: Request) -> JSONResponse:
    """Liveness probe.

    Intentionally does NOT contact Leantime. Reports the local MCP process
    is alive so Docker/HEALTHCHECK and reverse proxies can distinguish a
    crashed container from an upstream Leantime outage.
    """
    return JSONResponse({"status": "ok"})


@app.tool()
async def get_project(project_id: int) -> str:
    """Get details of a specific project by ID."""
    client = get_client()
    result = await client.get_project(project_id)
    return json.dumps(result, indent=2)


@app.tool()
async def list_projects() -> str:
    """List all projects accessible to the user."""
    client = get_client()
    result = await client.list_projects()
    return json.dumps(result, indent=2)


@app.tool()
async def create_project(name: str, details: str = None, clientId: int = None) -> str:
    """Create a new project."""
    client = get_client()
    result = await client.create_project(name=name, details=details, clientId=clientId)
    return json.dumps(result, indent=2)


@app.tool()
async def get_ticket(ticket_id: int) -> str:
    """Get details of a specific ticket by ID."""
    client = get_client()
    result = await client.get_ticket(ticket_id)
    return json.dumps(result, indent=2)


@app.tool()
async def list_tickets(project_id: int = None) -> str:
    """List tickets, optionally filtered by project ID."""
    client = get_client()
    result = await client.list_tickets(project_id)
    return json.dumps(result, indent=2)


@app.tool()
async def create_ticket(headline: str, project_id: int, user_id: int,
                       date: Optional[str] = None,
                       description: Optional[str] = None,
                       status: Optional[int] = None,
                       priority: Optional[str] = None,
                       assignedTo: Optional[int] = None,
                       tags: Optional[str] = None,
                       milestone_id: Optional[int] = None,
                       sprint_id: Optional[int] = None) -> str:
    """Create a new ticket.

    Args:
        status: Status ID (int) matching one of the IDs returned by get_status_labels.
        assignedTo: User ID (int) to assign the ticket to.
        milestone_id: Optional milestone (returned by list_milestones) to attach the ticket to.
        sprint_id: Optional sprint (returned by list_sprints) to attach the ticket to.
    """
    client = get_client()
    result = await client.create_ticket(
        headline=headline, project_id=project_id, user_id=user_id, date=date,
        description=description, status=status, priority=priority,
        assignedTo=assignedTo, tags=tags,
        milestone_id=milestone_id, sprint_id=sprint_id,
    )
    return json.dumps(result, indent=2)


@app.tool()
async def update_ticket(ticket_id: int, project_id: int,
                       headline: Optional[str] = None,
                       description: Optional[str] = None,
                       status: Optional[int] = None,
                       priority: Optional[str] = None,
                       assignedTo: Optional[int] = None,
                       milestone_id: Optional[int] = None,
                       sprint_id: Optional[int] = None) -> str:
    """Update an existing ticket.

    Pass milestone_id=0 or sprint_id=0 to detach the ticket from its current
    milestone or sprint respectively.
    """
    client = get_client()
    kwargs: dict = {}
    if headline is not None:
        kwargs['headline'] = headline
    if description is not None:
        kwargs['description'] = description
    if status is not None:
        kwargs['status'] = status
    if priority is not None:
        kwargs['priority'] = priority
    if assignedTo is not None:
        kwargs['assignedTo'] = assignedTo

    result = await client.update_ticket(
        ticket_id, project_id,
        milestone_id=milestone_id, sprint_id=sprint_id,
        **kwargs,
    )
    return json.dumps(result, indent=2)


@app.tool()
async def get_status_labels() -> str:
    """Get available status labels."""
    client = get_client()
    result = await client.get_status_labels()
    return json.dumps(result, indent=2)


@app.tool()
async def list_milestones(project_id: int, sort_by: str = "standard") -> str:
    """List all milestones for a project.

    Returns the milestone records (Leantime models milestones as tickets with
    type=milestone) including headlines, dates, status, and progress fields.
    """
    client = get_client()
    result = await client.list_milestones(project_id, sort_by=sort_by)
    return json.dumps(result, indent=2)


@app.tool()
async def create_milestone(headline: str, project_id: int, editor_id: int,
                           edit_from: Optional[str] = None,
                           edit_to: Optional[str] = None,
                           tags: Optional[str] = None,
                           dependent_milestone: Optional[int] = None) -> str:
    """Create a milestone in a project.

    Args:
        headline: Milestone title.
        project_id: Project this milestone belongs to.
        editor_id: User ID owning the milestone (typically the creator).
        edit_from: Optional start date (YYYY-MM-DD).
        edit_to: Optional end / due date (YYYY-MM-DD).
        tags: Optional comma-separated tag list.
        dependent_milestone: Optional ID of a milestone this one depends on.

    Returns the new milestone ID as a JSON-encoded integer.
    """
    client = get_client()
    result = await client.create_milestone(
        headline=headline, project_id=project_id, editor_id=editor_id,
        edit_from=edit_from, edit_to=edit_to, tags=tags,
        dependent_milestone=dependent_milestone,
    )
    return json.dumps(result, indent=2)


@app.tool()
async def update_milestone(milestone_id: int, editor_id: int,
                           headline: Optional[str] = None,
                           edit_from: Optional[str] = None,
                           edit_to: Optional[str] = None,
                           status: Optional[int] = None,
                           tags: Optional[str] = None) -> str:
    """Update a milestone's lightweight fields (headline, dates, status, tags).

    editor_id is required by Leantime for activity attribution.
    """
    client = get_client()
    result = await client.update_milestone(
        milestone_id=milestone_id, editor_id=editor_id, headline=headline,
        edit_from=edit_from, edit_to=edit_to, status=status, tags=tags,
    )
    return json.dumps(result, indent=2)


@app.tool()
async def delete_milestone(milestone_id: int) -> str:
    """Delete a milestone by its ID."""
    client = get_client()
    result = await client.delete_milestone(milestone_id)
    return json.dumps(result, indent=2)


@app.tool()
async def get_user(user_id: int) -> str:
    """Get details of a specific user by ID."""
    client = get_client()
    result = await client.get_user(user_id)
    return json.dumps(result, indent=2)


@app.tool()
async def list_users() -> str:
    """List all users."""
    client = get_client()
    result = await client.list_users()
    return json.dumps(result, indent=2)


@app.tool()
async def get_user_by_email(email: str) -> str:
    """Get details of a specific user by their email address."""
    client = get_client()
    result = await client.get_user_by_email(email)
    return json.dumps(result, indent=2)


@app.tool()
async def add_comment(module: str, module_id: int, comment: str) -> str:
    """Add a comment to a module (ticket, project, etc.)."""
    client = get_client()
    result = await client.add_comment(module=module, module_id=module_id, comment=comment)
    return json.dumps(result, indent=2)


@app.tool()
async def get_comments(module: str, module_id: int) -> str:
    """Get comments for a module (ticket, project, etc.)."""
    client = get_client()
    result = await client.get_comments(module=module, module_id=module_id)
    return json.dumps(result, indent=2)


@app.tool()
async def add_timesheet(user_id: int, ticket_id: int, hours: float, date: str, description: str = None) -> str:
    """Add a timesheet entry."""
    client = get_client()
    result = await client.add_timesheet(
        user_id=user_id, ticket_id=ticket_id, hours=hours, date=date, description=description
    )
    return json.dumps(result, indent=2)


@app.tool()
async def get_timesheets(project_id: int = None, user_id: int = None) -> str:
    """Get timesheets, optionally filtered by project or user."""
    client = get_client()
    result = await client.get_timesheets(project_id=project_id, user_id=user_id)
    return json.dumps(result, indent=2)


@app.tool()
async def get_all_subtasks(ticket_id: int) -> str:
    """Get all subtasks for a ticket."""
    client = get_client()
    result = await client.get_all_subtasks(ticket_id)
    return json.dumps(result, indent=2)


@app.tool()
async def upsert_subtask(parent_ticket: int, headline: str,
                        date: Optional[str] = None,
                        description: Optional[str] = None,
                        status: Optional[int] = None,
                        priority: Optional[str] = None,
                        assignedTo: Optional[int] = None,
                        tags: Optional[str] = None) -> str:
    """Create or update a subtask.

    Args:
        status: Status ID (int) matching one of the IDs returned by get_status_labels.
        assignedTo: User ID (int) to assign the subtask to.
    """
    client = get_client()
    result = await client.upsert_subtask(
        parent_ticket_id=parent_ticket, headline=headline,
        date=date, description=description, status=status, priority=priority,
        assignedTo=assignedTo, tags=tags
    )
    return json.dumps(result, indent=2)


# ---- Sprints ----

@app.tool()
async def list_sprints(project_id: int) -> str:
    """List all sprints for a project."""
    client = get_client()
    return json.dumps(await client.list_sprints(project_id), indent=2)


@app.tool()
async def get_sprint(sprint_id: int) -> str:
    """Fetch a single sprint by ID."""
    client = get_client()
    return json.dumps(await client.get_sprint(sprint_id), indent=2)


@app.tool()
async def get_current_sprint_id(project_id: int) -> str:
    """Return the ID of the currently active sprint for a project, or false if none."""
    client = get_client()
    return json.dumps(await client.get_current_sprint_id(project_id), indent=2)


@app.tool()
async def list_future_sprints(project_id: int) -> str:
    """List all future (not-yet-started) sprints for a project."""
    client = get_client()
    return json.dumps(await client.list_future_sprints(project_id), indent=2)


@app.tool()
async def create_sprint(name: str, project_id: int, start_date: str, end_date: str) -> str:
    """Create a new sprint. Dates are YYYY-MM-DD."""
    client = get_client()
    return json.dumps(await client.create_sprint(name, project_id, start_date, end_date), indent=2)


@app.tool()
async def update_sprint(sprint_id: int,
                        name: Optional[str] = None,
                        project_id: Optional[int] = None,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None) -> str:
    """Update an existing sprint's name, project, or date range."""
    client = get_client()
    return json.dumps(
        await client.update_sprint(sprint_id, name=name, project_id=project_id,
                                   start_date=start_date, end_date=end_date),
        indent=2,
    )


# ---- Goalcanvas ----

@app.tool()
async def list_goals(project_id: Optional[int] = None,
                     board_id: Optional[int] = None) -> str:
    """List Goalcanvas goals, optionally filtered by project and/or board."""
    client = get_client()
    return json.dumps(await client.list_goals(project_id=project_id, board_id=board_id), indent=2)


@app.tool()
async def list_goal_board_items(board_id: int) -> str:
    """List all goal items on a Goalcanvas board, including computed progress."""
    client = get_client()
    return json.dumps(await client.list_goal_board_items(board_id), indent=2)


@app.tool()
async def create_goal(title: str, project_id: int, board_id: int,
                      description: Optional[str] = None,
                      current_value: Optional[float] = None,
                      start_value: Optional[float] = None,
                      end_value: Optional[float] = None,
                      assigned_to: Optional[int] = None,
                      parent: Optional[int] = None) -> str:
    """Create a goal on a Goalcanvas board.

    Args:
        title: Goal headline.
        project_id: Project the goal belongs to.
        board_id: Goalcanvas board to add the goal to.
        description: Optional long-form description.
        current_value / start_value / end_value: Optional metric tracking
            fields for progress calculation.
        assigned_to: Optional user ID to assign the goal to.
        parent: Optional parent goal ID (for KPI hierarchies).
    """
    values: dict = {
        "title": title,
        "projectId": project_id,
        "board": board_id,
    }
    if description is not None:
        values["description"] = description
    if current_value is not None:
        values["currentValue"] = current_value
    if start_value is not None:
        values["startValue"] = start_value
    if end_value is not None:
        values["endValue"] = end_value
    if assigned_to is not None:
        values["assignedTo"] = assigned_to
    if parent is not None:
        values["parent"] = parent
    client = get_client()
    return json.dumps(await client.create_goal(values), indent=2)


# ---- Files (read + delete; upload requires multipart, not RPC-callable) ----

@app.tool()
async def list_files_for_module(module: str, entity_id: int,
                                user_id: Optional[int] = None) -> str:
    """List files attached to a module entity.

    `module` is a Leantime module name like 'ticket', 'project', or
    'canvas'; `entity_id` is the ID within that module. NOTE: file
    upload is not exposed via JSON-RPC (Leantime's upload endpoint
    requires multipart). Use Leantime's web UI to attach files.
    """
    client = get_client()
    return json.dumps(
        await client.list_files_for_module(module, entity_id, user_id=user_id),
        indent=2,
    )


@app.tool()
async def delete_file(file_id: int) -> str:
    """Delete a file attachment by its file ID."""
    client = get_client()
    return json.dumps(await client.delete_file(file_id), indent=2)


# ---- Wiki (read-only; create/update require PHP model objects) ----

@app.tool()
async def list_wikis(project_id: int) -> str:
    """List wiki spaces in a project.

    NOTE: creating new wiki spaces or articles is not exposed via the
    MCP layer because Leantime's Wiki create methods accept PHP model
    objects rather than JSON. Use the web UI to author wiki content.
    """
    client = get_client()
    return json.dumps(await client.list_wikis(project_id), indent=2)


@app.tool()
async def get_wiki(wiki_id: int) -> str:
    """Fetch metadata for a single wiki space."""
    client = get_client()
    return json.dumps(await client.get_wiki(wiki_id), indent=2)


@app.tool()
async def list_wiki_articles(wiki_id: int, user_id: int) -> str:
    """List article headlines in a wiki space."""
    client = get_client()
    return json.dumps(await client.list_wiki_articles(wiki_id, user_id), indent=2)


@app.tool()
async def get_wiki_article(article_id: int,
                           project_id: Optional[int] = None) -> str:
    """Fetch a wiki article's full content."""
    client = get_client()
    return json.dumps(await client.get_wiki_article(article_id, project_id=project_id), indent=2)


@app.tool()
async def get_wiki_article_history(article_id: int, limit: int = 20) -> str:
    """Fetch the revision history for a wiki article (most recent first)."""
    client = get_client()
    return json.dumps(await client.get_wiki_article_history(article_id, limit=limit), indent=2)


# ---- Ideas (read-only polling) ----

@app.tool()
async def list_new_ideas(project_id: Optional[int] = None,
                         board_id: Optional[int] = None) -> str:
    """Poll for newly created ideas, optionally scoped to project and board.

    NOTE: idea creation / editing is not exposed via the MCP layer (the
    Leantime Ideas service does not expose CRUD endpoints over JSON-RPC).
    Use the web UI to manage ideas.
    """
    client = get_client()
    return json.dumps(
        await client.list_new_ideas(project_id=project_id, board_id=board_id),
        indent=2,
    )


@app.tool()
async def list_updated_ideas(project_id: Optional[int] = None,
                             board_id: Optional[int] = None) -> str:
    """Poll for recently modified ideas, optionally scoped to project and board."""
    client = get_client()
    return json.dumps(
        await client.list_updated_ideas(project_id=project_id, board_id=board_id),
        indent=2,
    )


def main():
    """Main entry point for the MCP server."""
    _configure_logging()
    app.run()


if __name__ == "__main__":
    main()
