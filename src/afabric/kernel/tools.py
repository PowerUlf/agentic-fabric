"""The tool catalog — the one place tool names and argument shapes are written down.

Core MCP is in preview and its surface may change before GA. Keeping the mapping here
means a rename is a one-line edit, never a hunt through module code.

Two things differ between the transports and are reconciled here, not by callers:

- **Argument names.** Core MCP takes PascalCase (`WorkspaceId`); the REST paths use
  camelCase. Modules write the camelCase form and `mcp_args` translates.
- **Pagination.** Both transports page, with a `ContinuationToken` argument and a
  `continuationToken` in the response. `paged` says which tools need the loop; getting
  this wrong truncates results silently, which is worse than an error.
- **Request bodies.** Canonical arguments follow REST: path parameters plus flat body
  fields. Most Core MCP write tools want the body nested under `Details`, but not all —
  `create_workspace` takes it flat. `mcp_body` records which.

Some operations have no Core MCP tool at all (assigning a capacity to an existing
workspace). Those carry only `rest`, and the ToolBus serves them over REST whatever the
configured transport — the gap-filling native tools ADR 0001 anticipates.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RestOp:
    """How to serve a tool over the plain REST API."""

    method: str
    path: str
    """Path under the API base. May contain `{placeholders}` filled from tool args."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    mcp_tool: str | None = None
    rest: RestOp | None = None

    mcp_args: dict[str, str] = field(default_factory=dict)
    """Canonical argument name -> the name Core MCP expects."""

    paged: bool = False
    """Whether this tool returns a paginated collection."""

    collection: str = "value"
    """Response key holding the result list. Only meaningful when `paged`."""

    mcp_body: str | None = None
    """Core MCP key under which non-path arguments are nested, e.g. `Details`.

    Path arguments are exactly those named in `mcp_args`; everything else is body.
    """


_WORKSPACE_ID = {"workspaceId": "WorkspaceId"}

CATALOG: dict[str, ToolSpec] = {
    "list_workspaces": ToolSpec(
        name="list_workspaces",
        description="List all workspaces the caller can access.",
        mcp_tool="list_workspaces",
        rest=RestOp("GET", "/workspaces"),
        paged=True,
    ),
    "get_workspace": ToolSpec(
        name="get_workspace",
        description="Get one workspace by id.",
        mcp_tool="get_workspace",
        rest=RestOp("GET", "/workspaces/{workspaceId}"),
        mcp_args=_WORKSPACE_ID,
    ),
    "list_capacities": ToolSpec(
        name="list_capacities",
        description="List all Fabric capacities the caller can access.",
        mcp_tool="list_capacities",
        rest=RestOp("GET", "/capacities"),
        paged=True,
    ),
    "list_items": ToolSpec(
        name="list_items",
        description="List items in a workspace.",
        mcp_tool="list_items",
        rest=RestOp("GET", "/workspaces/{workspaceId}/items"),
        mcp_args=_WORKSPACE_ID | {"type": "Type"},
        paged=True,
    ),
    "list_folders": ToolSpec(
        name="list_folders",
        description="List folders in a workspace.",
        mcp_tool="list_folders",
        rest=RestOp("GET", "/workspaces/{workspaceId}/folders"),
        mcp_args=_WORKSPACE_ID,
        paged=True,
    ),
    "list_workspace_roles": ToolSpec(
        name="list_workspace_roles",
        description="List role assignments on a workspace.",
        mcp_tool="list_workspace_roles",
        rest=RestOp("GET", "/workspaces/{workspaceId}/roleAssignments"),
        mcp_args=_WORKSPACE_ID,
        paged=True,
    ),
    "list_item_job_instances": ToolSpec(
        name="list_item_job_instances",
        description="List recent job runs of one item, newest first.",
        rest=RestOp("GET", "/workspaces/{workspaceId}/items/{itemId}/jobs/instances"),
        paged=True,
        # Core MCP offers no job-scheduler tool; served over REST on every transport.
    ),
    "list_item_schedules": ToolSpec(
        name="list_item_schedules",
        description="List the schedules of one item for one job type.",
        rest=RestOp("GET", "/workspaces/{workspaceId}/items/{itemId}/jobs/{jobType}/schedules"),
        paged=True,
    ),
    # --- writes ---------------------------------------------------------------
    "create_workspace": ToolSpec(
        name="create_workspace",
        description="Create a workspace, optionally on a capacity.",
        mcp_tool="create_workspace",
        rest=RestOp("POST", "/workspaces"),
        # Flat in MCP too: displayName, description, capacityId.
    ),
    "update_workspace": ToolSpec(
        name="update_workspace",
        description="Change a workspace's display name or description.",
        mcp_tool="update_workspace",
        rest=RestOp("PATCH", "/workspaces/{workspaceId}"),
        mcp_args=_WORKSPACE_ID,
        mcp_body="Details",
    ),
    "delete_workspace": ToolSpec(
        name="delete_workspace",
        description="Delete a workspace and everything in it.",
        mcp_tool="delete_workspace",
        rest=RestOp("DELETE", "/workspaces/{workspaceId}"),
        mcp_args=_WORKSPACE_ID,
    ),
    "assign_to_capacity": ToolSpec(
        name="assign_to_capacity",
        description="Move an existing workspace onto a capacity.",
        rest=RestOp("POST", "/workspaces/{workspaceId}/assignToCapacity"),
        # No Core MCP tool exists for this; served over REST on every transport.
    ),
    "create_folder": ToolSpec(
        name="create_folder",
        description="Create a folder in a workspace.",
        mcp_tool="create_folder",
        rest=RestOp("POST", "/workspaces/{workspaceId}/folders"),
        mcp_args=_WORKSPACE_ID,
        mcp_body="Details",
    ),
    "delete_folder": ToolSpec(
        name="delete_folder",
        description="Delete an empty folder.",
        mcp_tool="delete_folder",
        rest=RestOp("DELETE", "/workspaces/{workspaceId}/folders/{folderId}"),
        mcp_args=_WORKSPACE_ID | {"folderId": "FolderId"},
    ),
    "add_workspace_role": ToolSpec(
        name="add_workspace_role",
        description="Grant a principal a role on a workspace.",
        mcp_tool="add_workspace_role",
        rest=RestOp("POST", "/workspaces/{workspaceId}/roleAssignments"),
        mcp_args=_WORKSPACE_ID,
        mcp_body="Details",
    ),
    "update_workspace_role": ToolSpec(
        name="update_workspace_role",
        description="Change the role of an existing assignment.",
        mcp_tool="update_workspace_role",
        rest=RestOp("PATCH", "/workspaces/{workspaceId}/roleAssignments/{roleAssignmentId}"),
        mcp_args=_WORKSPACE_ID | {"roleAssignmentId": "RoleAssignmentId"},
        mcp_body="Details",
    ),
    "delete_workspace_role": ToolSpec(
        name="delete_workspace_role",
        description="Revoke a role assignment.",
        mcp_tool="delete_workspace_role",
        rest=RestOp("DELETE", "/workspaces/{workspaceId}/roleAssignments/{roleAssignmentId}"),
        mcp_args=_WORKSPACE_ID | {"roleAssignmentId": "RoleAssignmentId"},
    ),
}


def spec(name: str) -> ToolSpec:
    try:
        return CATALOG[name]
    except KeyError:
        raise KeyError(
            f"unknown tool {name!r}; known tools: {', '.join(sorted(CATALOG))}"
        ) from None
