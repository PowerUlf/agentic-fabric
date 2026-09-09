# Architecture

## The shape of it

```
        Intent (natural language / fabric.yaml)
                  │
    ┌─────────────▼──────────────────────────────┐
    │  Kernel                                    │
    │  agent-loop · policy · journal · state     │
    └─────────────┬──────────────────────────────┘
                  │  ToolBus
        ┌─────────┴─────────┐
        ▼                   ▼
  Fabric Core MCP     native tools (REST / SDK)
```

## Kernel

The kernel is use-case free. It holds no knowledge of workspaces, notebooks or
pipelines — only of tools, policy, journal and the agent loop.

| Component | Responsibility |
|---|---|
| `config.py` | settings; merges `fabric.yaml` with `fabric.d/*.yaml` |
| `modules.py` | module discovery, manifests, capability registration |
| `auth.py` | Entra credentials — service principal or interactive |
| `mcp_client.py` | MCP client against Fabric Core MCP (streamable HTTP) |
| `rest_client.py` | direct REST: pagination, long-running operations, 429 retry |
| `toolbus.py` | tool registry; picks the MCP or REST backend per call |
| `policy.py` | guardrails: deny rules, blast radius, dry-run |
| `journal.py` | append-only JSONL audit of every planned and applied change |
| `agent.py` | the agent loop (Anthropic Messages API) over the ToolBus |

## The module contract

Every module lives in its own directory and exposes the same four things, all optional
except the manifest and the process:

```python
# manifest.py
MANIFEST = ModuleManifest(
    name="workspace",
    version="0.1.0",
    requires=["fabric.core"],      # capabilities it consumes
    provides=["workspace.ensure"], # capabilities it offers to others
    config_key="workspaces",       # its top-level key in fabric.yaml
)
```

```python
# process.py
def plan(desired, observed) -> list[Change]: ...   # pure, no I/O
def apply(changes, toolbus) -> list[Result]: ...
```

`plan()` being pure is what makes the whole system testable against recorded fixtures
without a tenant.

### Rules the contract enforces

1. **A module is a directory.** Discovery happens through the `afabric.modules` entry
   point group plus a scan. There is no registry file to edit.
2. **One `Change` type for everyone.** Policy, approval, dry-run, journal and the UI
   work on `Change`, so they work identically for every module present and future.
3. **No module imports another.** Cross-module dependencies are declared as capabilities
   in the manifest and resolved by the ToolBus at runtime.
4. **Config is namespaced.** A module validates its own key and nothing else. Modules
   without configuration cost nothing.

The measure of whether this is right: adding the second module should cost no more than
creating a directory. If it costs more, the abstraction is wrong and gets fixed before a
third module exists.

## Change and risk

```python
Change(
    module="workspace",
    action="role.revoke",       # module-defined verb
    target="Sales Analytics",
    risk=Risk.DESTRUCTIVE,      # SAFE | REVERSIBLE | DESTRUCTIVE
    before={...}, after={...},
)
```

Risk is what policy reasons about. `DESTRUCTIVE` changes require explicit approval
regardless of which module produced them.

## Transports

| | MCP | REST |
|---|---|---|
| Auth | Entra OAuth, browser | service principal, client credentials |
| Use | interactive, a human present | unattended agents, scheduled runs |
| Audit | Fabric audit log + our journal | our journal |

Both are reached through the same ToolBus signatures. Choosing between them is
configuration (`AFABRIC_TRANSPORT`), not a code path modules ever see.
