"""The `afab` command line.

Phase 0: the shape only. Commands print what they will do and exit; the kernel that
backs them arrives in phases 1-3.
"""

from __future__ import annotations

import typer
from rich.console import Console

from afabric import __version__

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
def modules() -> None:
    """List the modules the kernel discovered."""
    console.print(f"modules: {_NOT_YET} (phase 2)")


@app.command()
def status() -> None:
    """Show workspaces and capacities in the connected tenant."""
    console.print(f"status: {_NOT_YET} (phase 1)")


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
