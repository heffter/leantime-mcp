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


def main():
    """Main entry point for the MCP server."""
    _configure_logging()
    app.run()


if __name__ == "__main__":
    main()
