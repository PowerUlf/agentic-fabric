"""The tool catalog — the one place tool names and argument shapes are written down.

Core MCP is in preview and its surface may change before GA. Keeping the mapping here
means a rename is a one-line edit, never a hunt through module code.

Two things differ between the transports and are reconciled here, not by callers:

- **Argument names.** Core MCP takes PascalCase (`WorkspaceId`); the REST paths use
  camelCase. Modules write the camelCase form and `mcp_args` translates.
- **Pagination.** Both transports page, with a `ContinuationToken` argument and a
  `continuationToken` in the response. `paged` says which tools need the loop; getting
  this wrong truncates results silently, which is worse than an error.
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
}


def spec(name: str) -> ToolSpec:
    try:
        return CATALOG[name]
    except KeyError:
        raise KeyError(
            f"unknown tool {name!r}; known tools: {', '.join(sorted(CATALOG))}"
        ) from None
