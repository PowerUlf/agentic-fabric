"""Wiring: credentials -> transport -> ToolBus.

Everything above this line works with a ToolBus and never constructs a client.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from afabric.kernel.auth import acquire_token
from afabric.kernel.config import Settings, Transport
from afabric.kernel.mcp_client import mcp_session
from afabric.kernel.rest_client import RestClient
from afabric.kernel.toolbus import McpBackend, RestBackend, ToolBus


@asynccontextmanager
async def connect(
    settings: Settings,
    *,
    transport: Transport | None = None,
    interactive: str | None = None,
):
    """Open a ToolBus over the requested transport."""
    transport = transport or settings.transport

    token = acquire_token(settings, interactive=interactive)

    if transport is Transport.REST:
        async with RestClient(token, settings) as client:
            yield ToolBus(RestBackend(client), settings)
    else:
        async with mcp_session(token, settings) as session:
            yield ToolBus(McpBackend(session), settings)
