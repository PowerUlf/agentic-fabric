# agentic-fabric

An agentic operating system for Microsoft Fabric.

Agents that **operate the Fabric platform itself** — reconcile workspaces against a
declared desired state, watch job health, deploy items, audit governance — rather than
only answering questions about the data inside it.

> Status: early, but it runs against a live tenant. `afab plan` and `afab apply`
> reconcile workspaces, folders and roles behind policy, approval and an audit journal.
> `afab explain` has an agent explain observed drift, `afab propose` turns an intent in
> plain language into a `fabric.d/` fragment for you to review. Both only ever read —
> `afab apply` remains the one thing that changes the tenant. Next: more modules.

## Why this exists

Microsoft already ships the tool layer. The
[Fabric Core MCP Server](https://learn.microsoft.com/rest/api/fabric/articles/mcp-servers/core-remote/overview-core-mcp-server)
exposes workspaces, items, roles, folders and capacities as MCP tools; the local Fabric
MCP server, the RTI MCP server and published Data Agents cover more ground; Terraform and
`fabric-cicd` cover infrastructure and item deployment.

Another MCP server that forwards REST calls would be duplicated work. What is missing —
and what makes the word *operating system* mean anything — is the layer above:

**desired state · policy and guardrails · audit journal · scheduling · agent orchestration**

That is what this builds. Microsoft's servers are the device drivers.

## Architecture

```
        Intent (natural language / fabric.yaml)
                  │
    ┌─────────────▼──────────────────────────────┐
    │  Kernel                                    │
    │  agent-loop · policy · journal · state     │
    └─────────────┬──────────────────────────────┘
                  │  ToolBus (one tool surface)
        ┌─────────┴─────────┐
        ▼                   ▼
  Fabric Core MCP     native tools
  (OAuth, RBAC,       (REST/SDK for the gaps:
   audit log)          job health, capacity
                       metrics, fabric-cicd)
```

The kernel knows nothing about use cases. It knows tools, policy, journal and the agent
loop. Everything else is a module.

## Modules

A module is a directory, not a special case in the core:

```
src/afabric/modules/<name>/
├── manifest.py   # name, version, required capabilities, CLI verbs
├── tools.py      # tools it registers with the ToolBus        (optional)
├── model.py      # its slice of the fabric.yaml schema        (optional)
└── process.py    # plan() -> list[Change], apply()
```

Modules are discovered through the `afabric.modules` entry point group plus a scan of
the modules directory. Adding one means creating a directory — no registry file to edit,
no core to touch. Every module speaks the same `Change` type, passes through the same
policy, and writes to the same journal, so approval, audit and dry-run work identically
whether a change is a role assignment or a lakehouse schema.

Configuration cascades like `conf.d`: `fabric.yaml` plus `fabric.d/*.yaml`, each module
owning one top-level key.

Modules never import each other. They depend on capabilities declared at the ToolBus —
`workspace.ensure` is consumed by name, not by import.

Three exist: `workspace` reconciles workspaces, folders and roles; `job-health` watches
job runs and schedules; `governance` audits house rules. The last two read and plan —
their `apply` refuses rather than acting on something nothing has verified. Planned:
`deploy` and `data-engineering`.

The third module cost a directory and nothing else — no kernel edit, not even for its
tools. That was the measure the architecture set itself.

## Getting started

```bash
mise install          # pins Python 3.12 — the Fabric tools reject 3.13+
uv sync --extra dev
cp .env.example .env  # fill in, or use the interactive MCP transport
afab --help

afab status                                   # what the tenant holds
afab plan                                     # what applying fabric.yaml would change
afab apply                                    # apply it, behind policy and approval
afab explain                                  # have an agent explain the drift
afab propose "a dev environment for sales"    # intent -> a fabric.d/ fragment
```

The two agent commands run on the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview),
which signs in the way Claude Code does: a Claude plan login, or `CLAUDE_CODE_OAUTH_TOKEN`
from `claude setup-token` on a headless machine. An `ANTHROPIC_API_KEY` in the
environment outranks both and is billed per use.

## Documentation

- [Architecture](docs/architecture.md)
- [ADR 0001 — Build the kernel on Microsoft's MCP servers](docs/adr/0001-kernel-on-ms-mcp.md)

## License

MIT
