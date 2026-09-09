# agentic-fabric

An agentic operating system for Microsoft Fabric.

Agents that **operate the Fabric platform itself** — reconcile workspaces against a
declared desired state, watch job health, deploy items, audit governance — rather than
only answering questions about the data inside it.

> Status: early. Phase 0 (skeleton) of the plan. Nothing works yet.

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

Planned: `workspace` (first), then `job-health`, `deploy`, `governance`, and
`data-engineering`.

## Getting started

```bash
mise install          # pins Python 3.12 — the Fabric tools reject 3.13+
uv sync --extra dev
cp .env.example .env  # fill in, or use the interactive MCP transport
afab --help
```

## Documentation

- [Architecture](docs/architecture.md)
- [ADR 0001 — Build the kernel on Microsoft's MCP servers](docs/adr/0001-kernel-on-ms-mcp.md)

## License

MIT
