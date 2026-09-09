"""The `afab` command line."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from afabric import __version__
from afabric.kernel.auth import AuthError, forget
from afabric.kernel.config import Transport, load_settings
from afabric.kernel.session import connect

app = typer.Typer(
    name="afab",
    help="An agentic operating system for Microsoft Fabric.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_NOT_YET = "[yellow]not implemented yet[/] — see docs/architecture.md"


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"agentic-fabric {__version__}")


@app.command()
def status(
    transport: Annotated[
        Transport | None,
        typer.Option("--transport", "-t", help="Override the configured transport."),
    ] = None,
    browser: Annotated[
        bool,
        typer.Option("--browser", help="Sign in through a browser instead of a device code."),
    ] = False,
) -> None:
    """Show workspaces and capacities in the connected tenant."""
    settings = load_settings()
    chosen = transport or settings.transport

    try:
        workspaces, capacities = asyncio.run(
            _fetch_status(settings, chosen, "browser" if browser else "device")
        )
    except AuthError as exc:
        console.print(f"[red]sign-in failed:[/] {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        for line in _describe(exc):
            console.print(f"[red]{line}[/]")
        raise typer.Exit(1) from exc

    console.print(f"[dim]transport:[/] {chosen.value}")
    _render(
        "Workspaces",
        workspaces,
        [("Name", "displayName"), ("Id", "id"), ("Capacity", "capacityId")],
    )
    _render(
        "Capacities",
        capacities,
        [("Name", "displayName"), ("Id", "id"), ("SKU", "sku"), ("State", "state")],
    )


def _describe(exc: BaseException, depth: int = 0) -> list[str]:
    """Render an exception legibly, including grouped ones.

    The MCP transport runs on a TaskGroup, so a transport failure surfaces as
    `ExceptionGroup: unhandled errors in a TaskGroup (1 sub-exception)` — which says
    nothing at all. Walk into the group and report what actually went wrong.
    """
    indent = "  " * depth
    lines = [f"{indent}{type(exc).__name__}: {exc}"]
    for sub in getattr(exc, "exceptions", ()) or ():
        lines.extend(_describe(sub, depth + 1))
    if not getattr(exc, "exceptions", None) and exc.__cause__ is not None:
        lines.extend(_describe(exc.__cause__, depth + 1))
    return lines


async def _fetch_status(settings, transport: Transport, interactive: str):
    async with connect(settings, transport=transport, interactive=interactive) as bus:
        workspaces = await bus.call("list_workspaces")
        capacities = await bus.call("list_capacities")
        return _as_rows(workspaces), _as_rows(capacities)


def _as_rows(payload: Any) -> list[dict]:
    """Normalize a tool result to a list of records.

    The two backends do not agree on shape: REST returns the unwrapped collection,
    while Core MCP may hand back the whole response object or a bare list.
    """
    if payload is None:
        return []
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("value", "workspaces", "capacities", "items", "results"):
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
        return [payload]
    return []


def _render(title: str, rows: list[dict], columns: list[tuple[str, str]]) -> None:
    if not rows:
        console.print(f"\n[bold]{title}[/]  [dim]none[/]")
        return

    table = Table(title=f"{title} ({len(rows)})", title_justify="left", header_style="bold")
    for label, _ in columns:
        table.add_column(label, overflow="fold")
    for row in rows:
        table.add_row(*[str(row.get(key, "") or "") for _, key in columns])
    console.print()
    console.print(table)


@app.command()
def logout() -> None:
    """Forget the stored sign-in."""
    if forget():
        console.print("signed out")
    else:
        console.print("[dim]no stored sign-in[/]")


@app.command()
def modules() -> None:
    """List the modules the kernel discovered."""
    console.print(f"modules: {_NOT_YET} (phase 2)")


@app.command()
def plan() -> None:
    """Diff the desired state in fabric.yaml against the tenant."""
    console.print(f"plan: {_NOT_YET} (phase 2)")


@app.command()
def apply() -> None:
    """Apply a plan, after policy checks and approval."""
    console.print(f"apply: {_NOT_YET} (phase 3)")


@app.command()
def explain() -> None:
    """Have an agent explain the drift it observes."""
    console.print(f"explain: {_NOT_YET} (phase 4)")


if __name__ == "__main__":
    app()
