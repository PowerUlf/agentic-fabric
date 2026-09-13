"""The agent loop: Claude over the ToolBus, read-only.

The agent never computes drift and never changes anything. Planning stays with the
modules (`runner.plan`), applying stays behind policy and approval (`runner.apply`). What
the agent adds is the part a diff cannot: reading around a change and saying, in prose,
what it means and why it probably happened.

It runs on the Claude Agent SDK, which authenticates like Claude Code does — a Claude
plan login or `CLAUDE_CODE_OAUTH_TOKEN`, or an API key if one is set. The SDK's built-in
tools (shell, files, web) are all switched off; the only tools are an in-process MCP
server built here.

That server's tool surface is derived rather than listed: every catalog tool served by a
GET is offered, nothing else. A module adding a read tool extends the agent for free; a
write tool can never reach it, whatever the model asks for.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter
from typing import Any

from afabric.kernel.journal import read
from afabric.kernel.tools import CATALOG, ToolSpec
from afabric.model.change import Change

SERVER = "afab"
"""MCP server name. Claude sees each tool as `mcp__afab__<tool>`."""

JOURNAL_TOOL = "read_journal"

SYSTEM = """\
You explain configuration drift in a Microsoft Fabric tenant.

A declared desired state (fabric.yaml) was compared with what the tenant actually holds.
The comparison produced a list of changes: each one is a difference that applying the
desired state would remove. `before` is what the tenant holds now, `after` is what is
declared, and `risk` says how much applying it could damage (safe, reversible,
destructive).

Your job is to explain these differences to an operator:

- For each change, one short paragraph: what differs, in plain words, and what applying
  it would do.
- Then the likely cause. You may read the tenant and the afab journal to find out.
- Point out anything that makes applying risky, such as a destructive change or one that
  removes access.

On causes, separate what the evidence shows from what you infer. The journal records only
what afab did. It can show that afab set a value and that the value differs now; it
cannot show who changed it or how — a person in the portal, a script, another tool. Say
what the journal shows, then name the cause as a likely explanation, not as confirmed.

Stay with the changes in the list. Journal entries or tenant state unrelated to them are
out of scope, even when they look interesting.

You can only read. Do not propose commands to run, and do not claim to have changed
anything. Do not invent differences that are not in the list.

Write the explanation in {language}. Keep identifiers, field names and values as they are.
"""


@dataclass
class Proposal:
    filename: str
    yaml: str
    note: str
    turns: int
    tool_calls: list[str] = field(default_factory=list)
    cost_usd: float | None = None


@dataclass
class Explanation:
    text: str
    turns: int
    tool_calls: list[str] = field(default_factory=list)
    cost_usd: float | None = None
    """The SDK's estimate. On a Claude plan this is drawn from the plan, not billed."""


class AgentError(RuntimeError):
    """The agent could not produce an explanation."""


def read_only_tools() -> list[ToolSpec]:
    """Catalog tools that only read. Anything without a GET mapping is left out."""
    return [s for s in CATALOG.values() if s.rest is not None and s.rest.method == "GET"]


def tool_schema(spec: ToolSpec) -> dict[str, Any]:
    """JSON Schema for a catalog tool's arguments.

    Only path placeholders become arguments. Other arguments exist in the catalog (the
    `type` filter on `list_items`), but the REST backend does not send them on a GET, so
    offering them would hand the model silently unfiltered results on one transport.
    """
    names = sorted({name for _, name, _, _ in Formatter().parse(spec.rest.path) if name})
    return {
        "type": "object",
        "properties": {name: {"type": "string"} for name in names},
        "required": names,
        "additionalProperties": False,
    }


def build_tools(bus, journal_path: Path | str) -> list:
    """The agent's whole tool surface, as SDK MCP tools."""
    from claude_agent_sdk import ToolAnnotations, tool

    read_only = ToolAnnotations(readOnlyHint=True)
    tools = [
        tool(spec.name, spec.description, tool_schema(spec), annotations=read_only)(
            _bus_handler(bus, spec.name)
        )
        for spec in read_only_tools()
    ]
    tools.append(
        tool(
            JOURNAL_TOOL,
            "Read the most recent entries of the afab audit journal: what afab planned, "
            "approved and applied, with timestamps and run ids. Use it to tell whether a "
            "difference was introduced by afab or appeared afterwards.",
            {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "How many of the newest entries to return (1-200).",
                    }
                },
                "required": ["limit"],
                "additionalProperties": False,
            },
            annotations=read_only,
        )(_journal_handler(journal_path))
    )
    return tools


def _bus_handler(bus, name: str) -> Callable:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _result(await bus.call(name, **args))
        except Exception as exc:
            return _error(f"{name} failed: {type(exc).__name__}: {exc}")

    return handler


def _journal_handler(journal_path: Path | str) -> Callable:
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        try:
            limit = max(1, min(int(args.get("limit", 50)), 200))
            return _result(read(journal_path)[-limit:])
        except Exception as exc:
            return _error(f"reading the journal failed: {type(exc).__name__}: {exc}")

    return handler


def _result(payload: Any) -> dict[str, Any]:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}]}


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


PROPOSE_SYSTEM = """\
You turn an operator's intent into a desired-state fragment for afab.

afab reconciles a Microsoft Fabric tenant against a declared desired state. That state is
a `fabric.yaml` plus `fabric.d/*.yaml` files merged over it, each top-level key owned by
one module. Your answer is one such fragment: a new file for `fabric.d/`, declaring only
what the intent asks for. You never edit existing files and never apply anything — the
operator reviews your fragment, and `afab apply` remains the only thing that changes the
tenant.

How to work:

1. Call `declared_schema` for the shape each module's section must have. It is the
   authority — do not guess field names.
2. Read the tenant before you write. Capacity and workspace names must exist as spelled;
   `list_capacities` and `list_workspaces` show them. Declaring a workspace that already
   exists means afab will reconcile that one, which is rarely what a "new environment"
   intent means — say so rather than silently reusing it.
3. Call `validate_fragment` on your draft and fix what it reports.
4. Call `submit_proposal` with a `fabric.d/` filename and the final YAML. A numeric
   prefix orders the cascade, e.g. `20-team-sales.yaml`.

Leave out anything the intent does not ask for. Do not invent principals, ids or
capacities: when the intent needs something you cannot verify, leave the field out and
name it in your closing note.

After submitting, close with a short note in {language}: what you declared, what you had
to assume, and what the operator must fill in before applying.
"""


def build_propose_tools(bus, registry) -> tuple[list, dict[str, Any]]:
    """Tools for `propose`, plus the box the submitted proposal lands in."""
    from claude_agent_sdk import ToolAnnotations, tool

    box: dict[str, Any] = {}
    read_only = ToolAnnotations(readOnlyHint=True)
    tools = [
        tool(spec.name, spec.description, tool_schema(spec), annotations=read_only)(
            _bus_handler(bus, spec.name)
        )
        for spec in read_only_tools()
    ]

    @tool(
        "declared_schema",
        "JSON Schema for every top-level key the loaded modules own, plus the kernel's "
        "own `policy` key. The authority on what a fragment may contain.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        annotations=read_only,
    )
    async def declared_schema(args: dict[str, Any]) -> dict[str, Any]:
        try:
            return _result(schemas(registry))
        except Exception as exc:
            return _error(f"reading the schema failed: {type(exc).__name__}: {exc}")

    @tool(
        "validate_fragment",
        "Check a candidate fragment the way afab loads it: YAML syntax, whether a module "
        "owns each top-level key, and the module's own validation. Returns the problems.",
        {
            "type": "object",
            "properties": {"yaml": {"type": "string", "description": "The fragment's text."}},
            "required": ["yaml"],
            "additionalProperties": False,
        },
        annotations=read_only,
    )
    async def validate_fragment(args: dict[str, Any]) -> dict[str, Any]:
        problems = validate(args["yaml"], registry)
        return _result({"valid": not problems, "problems": problems})

    @tool(
        "submit_proposal",
        "Hand the finished fragment to the operator. Rejected unless it validates.",
        {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "File name for fabric.d/, e.g. 20-team-sales.yaml.",
                },
                "yaml": {"type": "string", "description": "The fragment's text."},
            },
            "required": ["filename", "yaml"],
            "additionalProperties": False,
        },
    )
    async def submit_proposal(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args["filename"]).strip()
        if "/" in name or "\\" in name or not name.endswith((".yaml", ".yml")):
            return _error(f"{name!r} must be a bare .yaml file name, no directories")
        problems = validate(args["yaml"], registry)
        if problems:
            return _error("the fragment does not validate: " + "; ".join(problems))
        box["filename"], box["yaml"] = name, args["yaml"]
        return _result({"accepted": name})

    tools.extend([declared_schema, validate_fragment, submit_proposal])
    return tools, box


def schemas(registry) -> dict[str, Any]:
    """JSON Schema per declared top-level key, read off the modules themselves."""
    from pydantic import TypeAdapter

    from afabric.kernel.desired import PolicyConfig

    found: dict[str, Any] = {"policy": PolicyConfig.model_json_schema()}
    for key, module in registry.by_config_key().items():
        model = module.component("model")
        config_type = getattr(model, "Config", None) if model else None
        found[key] = (
            TypeAdapter(config_type).json_schema()
            if config_type is not None
            else {"description": "this module validates nothing; any shape is accepted"}
        )
    return found


def validate(text: str, registry) -> list[str]:
    """Load a fragment the way `afab plan` would, and report what it complains about."""
    import tempfile

    from afabric.kernel.desired import DesiredStateError, load_desired

    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / "fabric.yaml"
        candidate.write_text(text, encoding="utf-8")
        try:
            load_desired(candidate, registry)
        except DesiredStateError as exc:
            return [p.replace(str(candidate), "fragment") for p in exc.problems]
    return []


def options(tools: list, *, model: str, max_turns: int, language: str, system: str = SYSTEM):
    from claude_agent_sdk import ClaudeAgentOptions, create_sdk_mcp_server

    server = create_sdk_mcp_server(name=SERVER, tools=tools)
    return ClaudeAgentOptions(
        system_prompt=system.format(language=language),
        model=model,
        max_turns=max_turns,
        thinking={"type": "adaptive"},
        # No built-in tools at all: no shell, no files, no web.
        tools=[],
        mcp_servers={SERVER: server},
        allowed_tools=[f"mcp__{SERVER}__{t.name}" for t in tools],
        # Anything not allowed above is denied, never prompted for.
        permission_mode="dontAsk",
        # Ignore ~/.claude and project settings: their hooks, skills and memory are the
        # user's Claude Code setup, not afab's.
        setting_sources=[],
        # Tool search would defer our tools behind the built-in ToolSearch, which
        # `tools=[]` removes. A handful of schemas fits in context anyway.
        env={"ENABLE_TOOL_SEARCH": "false"},
    )


async def explain(
    bus,
    changes: list[Change],
    journal_path: Path | str,
    *,
    model: str,
    language: str = "English",
    run_id: str | None = None,
    max_turns: int = 12,
    query: Callable | None = None,
) -> Explanation:
    if query is None:
        from claude_agent_sdk import query

    plan = json.dumps(
        [c.model_dump(mode="json", exclude={"metadata"}) for c in changes],
        indent=2,
        ensure_ascii=False,
    )
    prompt = f"The comparison found these changes:\n\n{plan}"
    if run_id:
        # The comparison journals its own `planned` entries before the agent starts.
        # Without this the agent reads them as an earlier run and dates the drift wrong.
        prompt += (
            f"\n\nThis comparison is journal run `{run_id}`. Its entries were written just "
            "now; they say nothing about when the drift appeared."
        )
    final, calls = await _drive(
        query,
        prompt,
        options(
            build_tools(bus, journal_path),
            model=model,
            max_turns=max_turns,
            language=language,
        ),
    )
    text = final.result if isinstance(final.result, str) else ""
    if final.is_error or not text.strip():
        raise AgentError(f"the agent stopped ({final.subtype}) without an explanation")
    return Explanation(
        text=text.strip(),
        turns=final.num_turns,
        tool_calls=calls,
        cost_usd=final.total_cost_usd,
    )


async def propose(
    bus,
    registry,
    intent: str,
    *,
    model: str,
    language: str = "English",
    max_turns: int = 20,
    query: Callable | None = None,
    build: Callable = build_propose_tools,
) -> Proposal:
    """Turn an intent into a validated `fabric.d/` fragment. Writes nothing."""
    if query is None:
        from claude_agent_sdk import query

    tools, box = build(bus, registry)
    final, calls = await _drive(
        query,
        f"The operator's intent:\n\n{intent}",
        options(
            tools,
            model=model,
            max_turns=max_turns,
            language=language,
            system=PROPOSE_SYSTEM,
        ),
    )
    if final.is_error and not box:
        raise AgentError(f"the agent stopped ({final.subtype}) without a proposal")
    if not box:
        raise AgentError("the agent finished without submitting a fragment")
    note = final.result.strip() if isinstance(final.result, str) else ""
    return Proposal(
        filename=box["filename"],
        yaml=box["yaml"],
        note=note,
        turns=final.num_turns,
        tool_calls=calls,
        cost_usd=final.total_cost_usd,
    )


async def _drive(query: Callable, prompt: str, opts) -> tuple[Any, list[str]]:
    """Run one agent loop to its result, collecting the tool names it called."""
    from claude_agent_sdk import AssistantMessage, ClaudeSDKError, ResultMessage, ToolUseBlock

    prefix = f"mcp__{SERVER}__"
    calls: list[str] = []
    final = None

    try:
        async for message in query(prompt=prompt, options=opts):
            if isinstance(message, AssistantMessage):
                calls.extend(
                    b.name.removeprefix(prefix)
                    for b in message.content
                    if isinstance(b, ToolUseBlock)
                )
            elif isinstance(message, ResultMessage):
                final = message
    except ClaudeSDKError as exc:
        raise AgentError(str(exc)) from exc

    if final is None:
        raise AgentError("the agent ended without a result")
    return final, calls
