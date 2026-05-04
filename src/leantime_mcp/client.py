# SPDX-FileCopyrightText: 2025 Daniel Eder
#
# SPDX-License-Identifier: MIT

"""Leantime JSON-RPC 2.0 client implementation."""

import httpx
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


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
    
    def _get_next_id(self) -> int:
        """Get next JSON-RPC request ID."""
        self._request_id += 1
        return self._request_id
    
    async def call(self, method: str, params: Optional[dict] = None) -> Any:
        """Make a JSON-RPC 2.0 call to Leantime API.
        
        Args:
            method: RPC method name (e.g., "leantime.rpc.Projects.getProject")
            params: Method parameters as dictionary
            
        Returns:
            The result from the JSON-RPC response
            
        Raises:
            LeantimeAPIError: If the API returns an error
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
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.endpoint,
                json=payload,
                headers=headers,
                timeout=30.0
            )
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
            
            # Return the result
            return data.get("result")
    
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
            **kwargs: Additional parameters
        """
        from datetime import datetime

        # Use current date if none provided
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

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
    
    async def update_ticket(self, ticket_id: int, project_id: int,
                            milestone_id: Optional[int] = None,
                            sprint_id: Optional[int] = None,
                            **kwargs) -> dict:
        """Update an existing ticket.

        Args:
            ticket_id: The ID of the ticket to update
            project_id: The project ID where the ticket belongs
            milestone_id: Optional milestone (ticket of type=milestone) to assign this ticket to.
                          Pass 0 to detach from any current milestone.
            sprint_id: Optional sprint ID to assign this ticket to. Pass 0 to detach.
            **kwargs: Additional parameters to update
        """
        values = {"id": ticket_id, "projectId": project_id, **kwargs}
        if milestone_id is not None:
            values["milestoneid"] = milestone_id
        if sprint_id is not None:
            values["sprint"] = sprint_id
        params = {"values": values}
        return await self.call("leantime.rpc.Tickets.Tickets.updateTicket", params)
    
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
    
    async def add_comment(self, module: str, module_id: int, comment: str) -> dict:
        """Add a comment to a module (e.g., ticket, project)."""
        params = {
            "module": module,
            "moduleId": module_id,
            "comment": comment
        }
        return await self.call("leantime.rpc.Comments.addComment", params)
    
    async def get_comments(self, module: str, module_id: int) -> list:
        """Get comments for a module."""
        params = {
            "module": module,
            "moduleId": module_id
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
    
    async def get_timesheets(self, project_id: Optional[int] = None, user_id: Optional[int] = None) -> list:
        """Get timesheet entries."""
        params = {}
        if project_id:
            params["projectId"] = project_id
        if user_id:
            params["userId"] = user_id
        return await self.call("leantime.rpc.Timesheets.getTimesheets", params)
    
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
        inner: dict = {
            "id": milestone_id,
            "editorId": editor_id,
            "headline": headline if headline is not None else current.get("headline", ""),
            "editFrom": edit_from if edit_from is not None else (current.get("editFrom") or ""),
            "editTo": edit_to if edit_to is not None else (current.get("editTo") or ""),
            "tags": tags if tags is not None else (current.get("tags") or ""),
            "status": status if status is not None else int(current.get("status") or 3),
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
