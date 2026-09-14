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
    # --- writes ---------------------------------------------------------------
    ToolSpec(
        name="create_item_schedule",
        description="Create a schedule for one item and job type. At most 20 per item.",
        rest=RestOp("POST", "/workspaces/{workspaceId}/items/{itemId}/jobs/{jobType}/schedules"),
    ),
    ToolSpec(
        name="update_item_schedule",
        description="Change an existing schedule. Replaces its whole configuration.",
        rest=RestOp(
            "PATCH",
            "/workspaces/{workspaceId}/items/{itemId}/jobs/{jobType}/schedules/{scheduleId}",
        ),
    ),
    ToolSpec(
        name="run_item_job",
        description="Start one on-demand run of an item's job.",
        rest=RestOp("POST", "/workspaces/{workspaceId}/items/{itemId}/jobs/{jobType}/instances"),
        # Answers 202 with a Location header and no operation id, so the ToolBus hands
        # back None. Started is all the API says; the run's outcome comes later.
    ),
]
