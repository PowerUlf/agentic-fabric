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
| `config.py` | settings from the environment and `.env` |
| `modules.py` | module discovery, manifests, capability registration |
| `desired.py` | the `fabric.yaml` + `fabric.d/` cascade, kernel policy |
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
# model.py — optional
Config = ...                                   # type its config section validates as
def merge(fragments: list[Fragment]) -> Any    # its own cascade merge; generic otherwise
```

```python
# process.py
async def observe(bus, desired) -> Observed      # the only I/O: read the tenant
def plan(desired, observed, policy) -> list[Change]  # pure
def project(observed, changes) -> Observed       # pure: the state after the changes
def apply(changes, bus) -> list[Result]          # phase 3
```

`plan()` being pure is what makes a module testable against recorded fixtures without
a tenant. `project()` makes idempotency checkable — planning against the projected
state must yield nothing — and is what a dry run shows.

`observe()` receives the desired state so it can skip what nothing declares. Anything it
did not look at must be marked as such, never left looking empty: `plan()` would read
an empty list as "everything is missing" and propose recreating it.

### Where modules come from

| Source | Found through | For |
|---|---|---|
| built-in | subpackages of `afabric.modules` | modules shipped with the kernel |
| entry point | the `afabric.modules` group | modules shipped as installed packages |
| module path | subdirectories of `AFABRIC_MODULE_PATH` (`:`-separated) | a directory on disk, nothing installed |

A problem anywhere — a manifest that fails to import, a name or config key claimed
twice, a required capability nobody provides — is collected and shown by `afab
modules`, which then exits non-zero. Nothing is skipped silently.

### Configuration

`fabric.yaml` is read first, then `fabric.d/*.yaml` in filename order. `version` and
`policy` belong to the kernel; every other top-level key belongs to exactly one module,
and a key no module owns is an error. Each module merges and validates only its own
fragments, which is how the workspace module can merge workspaces by name without the
kernel learning what a workspace is. All problems across all files are reported at once.

`policy.prune` is kernel-owned and applies to every module: without it, no module may
propose removing anything the desired state does not declare.

### Rules the contract enforces

1. **A module is a directory.** Discovery happens through the scan, the entry point
   group and the module path. There is no registry file to edit.
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
