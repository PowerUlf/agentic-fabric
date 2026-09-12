"""The `afab` command line."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from afabric import __version__
from afabric.kernel import runner
from afabric.kernel.auth import AuthError, forget
from afabric.kernel.config import Transport, load_settings
from afabric.kernel.desired import DesiredStateError, load_desired
from afabric.kernel.journal import Journal
from afabric.kernel.modules import discover
from afabric.kernel.session import connect
from afabric.model.change import Risk

app = typer.Typer(
    name="afab",
    help="An agentic operating system for Microsoft Fabric.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


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
    """List the modules the kernel discovered, and anything wrong with them."""
    settings = load_settings()
    registry = discover(settings.module_dirs)

    table = Table(title=f"Modules ({len(registry)})", title_justify="left", header_style="bold")
    for column in ("Name", "Version", "Config key", "Provides", "Requires", "Origin"):
        table.add_column(column, overflow="fold")
    for module in registry:
        manifest = module.manifest
        table.add_row(
            manifest.name,
            manifest.version,
            manifest.config_key or "[dim]—[/]",
            ", ".join(manifest.provides) or "[dim]—[/]",
            ", ".join(manifest.requires) or "[dim]—[/]",
            module.origin,
        )
    console.print(table)

    if registry.problems:
        console.print(f"\n[red bold]{len(registry.problems)} problem(s)[/]")
        for problem in registry.problems:
            console.print(f"  [red]•[/] {problem.source}: {problem.message}")
        raise typer.Exit(1)


FileOption = Annotated[
    Path, typer.Option("--file", "-f", help="Desired state file; fabric.d/ beside it is read too.")
]
TransportOption = Annotated[
    Transport | None,
    typer.Option("--transport", "-t", help="Override the configured transport."),
]

_RISK_STYLE = {Risk.SAFE: "green", Risk.REVERSIBLE: "yellow", Risk.DESTRUCTIVE: "red bold"}


@app.command()
def plan(
    file: FileOption = Path("fabric.yaml"),
    transport: TransportOption = None,
) -> None:
    """Show what applying the desired state would change. Changes nothing."""
    _run(file, transport, apply_changes=False, yes=False, approve_destructive=False)


@app.command()
def apply(
    file: FileOption = Path("fabric.yaml"),
    transport: TransportOption = None,
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Approve changes policy does not gate.")
    ] = False,
    approve_destructive: Annotated[
        bool,
        typer.Option(
            "--approve-destructive",
            help="Also approve changes policy gates. Denied changes stay denied.",
        ),
    ] = False,
) -> None:
    """Plan, check policy, ask for approval, and apply."""
    _run(file, transport, apply_changes=True, yes=yes, approve_destructive=approve_destructive)


def _run(
    file: Path,
    transport: Transport | None,
    *,
    apply_changes: bool,
    yes: bool,
    approve_destructive: bool,
) -> None:
    settings, registry, desired = _load(file)
    journal = Journal(settings.journal_path)
    try:
        code = asyncio.run(
            _session(
                settings,
                registry,
                desired,
                journal,
                transport or settings.transport,
                apply_changes=apply_changes,
                yes=yes,
                approve_destructive=approve_destructive,
            )
        )
    except AuthError as exc:
        console.print(f"[red]sign-in failed:[/] {exc}")
        raise typer.Exit(1) from exc
    except Exception as exc:
        for line in _describe(exc):
            console.print(f"[red]{line}[/]")
        raise typer.Exit(1) from exc
    raise typer.Exit(code)


async def _session(
    settings, registry, desired, journal, transport, *, apply_changes, yes, approve_destructive
) -> int:
    async with connect(settings, transport=transport) as bus:
        run = await runner.plan(bus, registry, desired, journal)
        _render_plan(run.evaluation, transport)

        if run.evaluation.refused:
            console.print(f"\n[red bold]refused:[/] {run.evaluation.refused}")
            return 1
        if not run.changes:
            console.print("\n[green]No changes.[/] The tenant matches the desired state.")
            return 0
        if not apply_changes:
            console.print("\n[dim]Nothing was changed. `afab apply` applies this plan.[/]")
            return 0

        approved = _approve(run.evaluation, yes=yes, approve_destructive=approve_destructive)
        if not approved:
            console.print("\n[yellow]Nothing approved, nothing applied.[/]")
            return 1

        outcomes = await runner.apply(bus, registry, approved, journal)
        _render_outcomes(outcomes, approved)
        complete = len(outcomes) == len(approved) and all(o.ok for o in outcomes)
        return 0 if complete else 1


def _approve(evaluation, *, yes: bool, approve_destructive: bool) -> list:
    interactive = sys.stdin.isatty()
    chosen: set[int] = set()

    allowed = evaluation.allowed
    if allowed:
        if yes or (interactive and typer.confirm(f"\nApply {len(allowed)} ungated change(s)?")):
            chosen.update(id(v.change) for v in allowed)
        elif not interactive:
            console.print(f"[yellow]{len(allowed)} ungated change(s) need --yes[/]")

    for verdict in evaluation.gated:
        change = verdict.change
        label = f"{change.action} {change.target} ({'; '.join(verdict.reasons)})"
        if approve_destructive:
            chosen.add(id(change))
        elif interactive and typer.confirm(f"Approve {label}?", default=False):
            chosen.add(id(change))
        else:
            console.print(f"[red]blocked:[/] {label} — needs explicit approval")

    for verdict in evaluation.denied:
        console.print(
            f"[red]denied:[/] {verdict.change.action} {verdict.change.target} "
            f"({'; '.join(verdict.reasons)})"
        )

    # Plan order, not approval order: later changes depend on earlier ones.
    return [v.change for v in evaluation.verdicts if id(v.change) in chosen]


def _render_plan(evaluation, transport: Transport) -> None:
    console.print(f"[dim]transport:[/] {transport.value}")
    verdicts = evaluation.verdicts
    if not verdicts:
        return

    table = Table(title=f"Plan ({len(verdicts)})", title_justify="left", header_style="bold")
    for column in ("Risk", "Action", "Target", "Change", "Policy"):
        table.add_column(column, overflow="fold")
    for verdict in verdicts:
        change = verdict.change
        if verdict.denied:
            policy = f"[red]denied[/] — {'; '.join(verdict.reasons)}"
        elif verdict.needs_approval:
            policy = f"[yellow]approval[/] — {'; '.join(verdict.reasons)}"
        else:
            policy = "[green]ok[/]"
        table.add_row(
            f"[{_RISK_STYLE[change.risk]}]{change.risk.value}[/]",
            change.action,
            change.target,
            _delta(change),
            policy,
        )
    console.print()
    console.print(table)
    console.print(
        f"{len(evaluation.allowed)} ok · {len(evaluation.gated)} need approval · "
        f"{len(evaluation.denied)} denied"
    )


def _delta(change) -> str:
    before, after = change.before or {}, change.after or {}
    keys = [k for k in {**before, **after} if before.get(k) != after.get(k)]
    if not keys:
        return ""
    return ", ".join(f"{k}: {before.get(k, '—')} → {after.get(k, '—')}" for k in keys)


def _render_outcomes(outcomes, approved) -> None:
    console.print()
    for outcome in outcomes:
        mark = "[green]✓[/]" if outcome.ok else "[red]✗[/]"
        tail = f" — {outcome.detail}" if outcome.detail else ""
        console.print(f"{mark} {outcome.change.action} {outcome.change.target}{tail}")
    skipped = len(approved) - len(outcomes)
    if skipped:
        console.print(f"[yellow]{skipped} change(s) not attempted after the failure[/]")


def _load(file: Path):
    """Settings, modules and desired state, or exit with what is wrong with them."""
    settings = load_settings()
    registry = discover(settings.module_dirs)
    if not registry.ok:
        console.print("[red bold]modules have problems — run `afab modules`[/]")
        raise typer.Exit(1)

    try:
        desired = load_desired(file, registry)
    except DesiredStateError as exc:
        console.print(f"[red bold]{len(exc.problems)} problem(s) in the desired state[/]")
        for problem in exc.problems:
            console.print(f"  [red]•[/] {problem}")
        raise typer.Exit(1) from exc
    return settings, registry, desired


@app.command()
def explain(
    file: FileOption = Path("fabric.yaml"),
    transport: TransportOption = None,
) -> None:
    """Have an agent explain the drift it observes. Changes nothing."""
    from afabric.kernel.agent import AgentError

    settings, registry, desired = _load(file)
    if os.environ.get("ANTHROPIC_API_KEY"):
        # Claude Code ranks an API key above a plan login and always uses it headless.
        console.print(
            "[yellow]ANTHROPIC_API_KEY is set — the agent bills that key, not your Claude "
            "plan. Unset it to use the plan.[/]"
        )
    journal = Journal(settings.journal_path)
    try:
        code = asyncio.run(
            _explain_session(settings, registry, desired, journal, transport or settings.transport)
        )
    except AuthError as exc:
        console.print(f"[red]sign-in failed:[/] {exc}")
        raise typer.Exit(1) from exc
    except AgentError as exc:
        console.print(f"[red]the agent failed:[/] {exc}")
        console.print(
            "[dim]Not signed in? Run `claude` once and log in with your Claude plan, "
            "or set CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`.[/]"
        )
        raise typer.Exit(1) from exc
    except Exception as exc:
        for line in _describe(exc):
            console.print(f"[red]{line}[/]")
        raise typer.Exit(1) from exc
    raise typer.Exit(code)


async def _explain_session(settings, registry, desired, journal, transport) -> int:
    from afabric.kernel import agent

    async with connect(settings, transport=transport) as bus:
        run = await runner.plan(bus, registry, desired, journal)
        _render_plan(run.evaluation, transport)
        if not run.changes:
            console.print("\n[green]No drift.[/] The tenant matches the desired state.")
            return 0

        # A refused run still gets explained: explaining changes nothing.
        with console.status(f"explaining with {settings.model}…"):
            result = await agent.explain(
                bus,
                run.changes,
                settings.journal_path,
                model=settings.model,
                language=settings.language,
                run_id=journal.run_id,
            )

    journal.record(
        "explained",
        model=settings.model,
        turns=result.turns,
        tool_calls=result.tool_calls,
        cost_usd=result.cost_usd,
        text=result.text,
    )
    console.print()
    console.print(result.text)
    tools = ", ".join(result.tool_calls) or "—"
    console.print(f"\n[dim]{result.turns} turn(s) · tools: {tools}[/]")
    return 0


if __name__ == "__main__":
    app()
