# ADR 0001 — Build the kernel on Microsoft's MCP servers

- **Status:** accepted
- **Date:** 2026-09-09

## Context

The first instinct for "agentic Fabric tooling" is to write an MCP server that exposes
the Fabric REST API as tools. Research into the current state of the platform showed
that surface is already covered:

| What | Kind | Covers |
|---|---|---|
| Fabric Core MCP Server | remote, preview, `https://api.fabric.microsoft.com/v1/mcp/core` | workspaces, items, roles, folders, capacities, catalog search, LRO polling |
| Fabric MCP Server (local) | open source, `microsoft/mcp` | offline API specs, OneLake files, item scaffolding |
| RTI MCP Server | open source, `microsoft/fabric-rti-mcp` | eventhouse, eventstream, Activator |
| Data agents | become MCP servers when published | Q&A grounded in OneLake |
| Terraform provider, `fabric-cicd` | — | infrastructure as code, item deployment |

Core MCP authenticates via Entra OAuth, enforces Fabric RBAC, and writes to the Fabric
audit log. Reimplementing that would mean re-earning properties we get for free, and
tracking API changes forever.

What no product covers is the layer above the tools: a declared desired state, drift
detection with explanation, policy and guardrails on destructive operations, an
append-only audit journal owned by us, scheduling, and orchestration across agents.

## Decision

Build the kernel on top of Microsoft's MCP servers. They are the device drivers; we
write the operating system.

Native tools are added only for genuine gaps — job health via the Job Scheduler API,
capacity metrics, deployment via `fabric-cicd`.

## Consequences

**Positive.** Far less code to own. RBAC enforcement, OAuth and audit logging come from
the platform. New Fabric item types appear in Core MCP without work on our side.

**Negative.** Core MCP is in preview; tool names may change before GA. This is contained
by routing every tool call through the ToolBus, so tool names live in exactly one place.

**A transport split, deliberately kept.** Core MCP authenticates with a browser OAuth
flow, which unattended agents cannot use. Those need service principal tokens against the
REST API. The ToolBus therefore abstracts over the *transport*, not the semantics: the
same tool signature, backed by MCP or REST depending on context. This is the only place
in the system where that duplication is allowed to exist.

**Not Terraform's job.** The Terraform provider remains the better choice for capacity
and tenant infrastructure. The reconciler here works one level up — intent instead of
HCL, explained drift instead of a plan diff, at item and role level, with an approval
workflow.
