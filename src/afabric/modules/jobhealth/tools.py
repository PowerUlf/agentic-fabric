"""The job-scheduler endpoints this module needs.

They live here rather than in the kernel catalog because nothing else uses them: the
kernel knows tools, not jobs. The ToolBus merges these with the catalog at connect time,
and discovery refuses a name that would shadow a catalog tool or another module's.

Core MCP offers no job-scheduler tool, so both carry only `rest` and are served over
REST whatever the configured transport.
"""

from afabric.kernel.tools import RestOp, ToolSpec

SPECS = [
    ToolSpec(
        name="list_item_job_instances",
        description="List recent job runs of one item. They come back unordered.",
        rest=RestOp("GET", "/workspaces/{workspaceId}/items/{itemId}/jobs/instances"),
        paged=True,
    ),
    ToolSpec(
        name="list_item_schedules",
        description="List the schedules of one item for one job type. A job type the "
        "item does not have is a 400, not an empty list.",
        rest=RestOp("GET", "/workspaces/{workspaceId}/items/{itemId}/jobs/{jobType}/schedules"),
        paged=True,
    ),
]
