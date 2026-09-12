"""One tool surface, two backends.

Modules call `bus.call("list_workspaces")` and never learn whether that was served by
Core MCP or a REST request. This is the only place in the system where the transport
split from ADR 0001 is visible.

The bus is also where capabilities live. A module declares `provides=["workspace.ensure"]`
in its manifest and registers the callable here; a consuming module resolves it by name.
That indirection is what keeps modules from importing each other.
"""

from __future__ import annotations

from string import Formatter
from typing import Any, Protocol

from afabric.kernel.config import Settings, Transport
from afabric.kernel.mcp_client import unwrap
from afabric.kernel.rest_client import RestClient
from afabric.kernel.tools import ToolSpec, spec


class Backend(Protocol):
    async def call(self, tool: ToolSpec, args: dict[str, Any]) -> Any: ...


class UnsupportedTool(RuntimeError):
    pass


class McpBackend:
    """Serves tools through the Fabric Core MCP Server."""

    name = "mcp"

    def __init__(self, session: Any) -> None:
        self._session = session

    async def call(self, tool: ToolSpec, args: dict[str, Any]) -> Any:
        if not tool.mcp_tool:
            raise UnsupportedTool(f"{tool.name} has no MCP equivalent")

        # Core MCP rejects a missing `arguments` member outright: passing None yields
        # "Invalid tool call params" even for tools that take no arguments at all.
        payload = _mcp_payload(tool, args)

        if not tool.paged:
            return unwrap(await self._session.call_tool(tool.mcp_tool, payload))

        items: list[Any] = []
        while True:
            body = unwrap(await self._session.call_tool(tool.mcp_tool, payload))
            if not isinstance(body, dict):
                # A server that answered with prose instead of data; hand it back
                # unchanged rather than pretending the collection was empty.
                return body if not items else items
            items.extend(body.get(tool.collection, []))

            token = body.get("continuationToken")
            if not token:
                return items
            payload = {**payload, "ContinuationToken": token}


class RestBackend:
    """Serves tools through the Fabric REST API."""

    name = "rest"

    def __init__(self, client: RestClient) -> None:
        self._client = client

    async def call(self, tool: ToolSpec, args: dict[str, Any]) -> Any:
        if not tool.rest:
            raise UnsupportedTool(f"{tool.name} has no REST equivalent")

        op = tool.rest
        path, leftover = _fill(op.path, args)

        if op.method != "GET":
            response = await self._client.request(op.method, path, json=leftover or None)
            # 202 means the work continues server-side; the result only exists once the
            # operation settles. Returning early would report success for a change that
            # may still fail.
            operation_id = response.headers.get("x-ms-operation-id")
            if response.status_code == 202 and operation_id:
                return await self._client.await_operation(operation_id)
            return response.json() if response.content else None

        if tool.paged:
            return await self._client.get_all(path, collection=tool.collection)
        return await self._client.get_one(path)


def _mcp_payload(tool: ToolSpec, args: dict[str, Any]) -> dict[str, Any]:
    """Shape canonical arguments the way this Core MCP tool wants them.

    Arguments named in `mcp_args` are path parameters and get renamed; the rest are body
    fields, nested under `mcp_body` when the tool expects that and flat otherwise.
    """
    payload: dict[str, Any] = {}
    body: dict[str, Any] = {}
    for key, value in args.items():
        if key in tool.mcp_args:
            payload[tool.mcp_args[key]] = value
        else:
            body[key] = value

    if tool.mcp_body and body:
        payload[tool.mcp_body] = body
    else:
        payload.update(body)
    return payload


def _fill(template: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Substitute `{placeholders}` from args; return the path and the unused args."""
    names = {name for _, name, _, _ in Formatter().parse(template) if name}
    missing = names - args.keys()
    if missing:
        raise KeyError(f"missing argument(s) for path {template!r}: {', '.join(sorted(missing))}")
    return template.format(**args), {k: v for k, v in args.items() if k not in names}


class ToolBus:
    def __init__(
        self,
        backend: Backend,
        settings: Settings,
        *,
        fallback: Backend | None = None,
        identity: str | None = None,
    ) -> None:
        self._backend = backend
        self._fallback = fallback
        self._settings = settings
        self._capabilities: dict[str, Any] = {}
        self.identity = identity
        """Entra object id of whoever this bus acts as, when the token reveals it."""

    @property
    def transport(self) -> Transport:
        return Transport(self._backend.name)

    async def call(self, tool_name: str, **args: Any) -> Any:
        tool = spec(tool_name)
        # A tool Core MCP does not offer is served over REST with the same identity.
        # Callers never see which one answered.
        if self._backend.name == "mcp" and not tool.mcp_tool and self._fallback is not None:
            return await self._fallback.call(tool, args)
        return await self._backend.call(tool, args)

    # --- capabilities ---------------------------------------------------------

    def provide(self, capability: str, impl: Any) -> None:
        if capability in self._capabilities:
            raise ValueError(f"capability {capability!r} is already provided")
        self._capabilities[capability] = impl

    def resolve(self, capability: str) -> Any:
        try:
            return self._capabilities[capability]
        except KeyError:
            raise KeyError(
                f"no module provides capability {capability!r}; "
                f"available: {', '.join(sorted(self._capabilities)) or 'none'}"
            ) from None

    def has(self, capability: str) -> bool:
        return capability in self._capabilities
