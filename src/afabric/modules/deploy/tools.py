"""The item-definition endpoint this module needs.

`getDefinition` is a POST that reads: it takes no body and changes nothing, but the
service answers 202 and hands the definition back through an operation. `RestBackend`
already waits that out, so callers see the finished definition.

Core MCP offers no definition tool, so this carries only `rest` and is served over REST
whatever the configured transport.
"""

from afabric.kernel.tools import RestOp, ToolSpec

SPECS = [
    ToolSpec(
        name="get_item_definition",
        description="Get one item's definition: its parts, each base64 encoded.",
        rest=RestOp("POST", "/workspaces/{workspaceId}/items/{itemId}/getDefinition"),
    )
]
