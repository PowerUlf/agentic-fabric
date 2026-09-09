"""MCP client against the Fabric Core MCP Server.

Core MCP normally authenticates through a browser OAuth flow driven by the host. We
sign in ourselves instead (device code by default, see `auth.py`) and attach the
resulting Fabric token as a bearer header, which is the pattern Microsoft documents for
custom MCP clients against Fabric endpoints.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

# mcp 2.x transports are built on httpx2; the REST client uses httpx 0.x. Keeping the
# import explicit avoids confusing the two.
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from afabric.kernel.config import Settings


class McpError(RuntimeError):
    pass


@asynccontextmanager
async def mcp_session(token: str, settings: Settings):
    """Open an initialized MCP session against Core MCP."""
    http_client = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    async with http_client:
        async with streamable_http_client(
            settings.mcp_endpoint, http_client=http_client
        ) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def unwrap(result: Any) -> Any:
    """Turn a CallToolResult into plain Python data.

    Servers may answer with structured content, with JSON in a text block, or with
    prose. Prefer structure, fall back to parsing, and surface text unchanged when it
    is not JSON at all.
    """
    if getattr(result, "isError", False):
        raise McpError(_text_of(result) or "tool call failed")

    structured = getattr(result, "structuredContent", None)
    if structured:
        return structured

    text = _text_of(result)
    if text is None:
        return None
    return parse_payload(text)


def parse_payload(text: str) -> Any:
    """Parse a text block that is JSON, possibly with trailing noise.

    Core MCP appends a diagnostic line after the JSON body:

        {"value":[...]}\nRequestId: 480bb85e-...

    A plain `json.loads` chokes on that and would hand every caller a string instead
    of data. `raw_decode` reads the leading value and ignores whatever follows.
    """
    stripped = text.lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(stripped)
        return value
    except json.JSONDecodeError:
        return text


def _text_of(result: Any) -> str | None:
    parts = [
        block.text
        for block in getattr(result, "content", []) or []
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(parts) if parts else None
