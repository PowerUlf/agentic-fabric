# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

The venv is auto-activated by mise (`.mise.toml` pins Python 3.12). Without mise
active in the shell, call binaries as `.venv/bin/<name>`.

```bash
mise install                    # first time, or after a fresh shell
uv sync --extra dev

pytest -q                       # all tests
pytest tests/test_toolbus.py::TestPathFilling::test_missing_argument_names_itself
ruff check .                    # lint; ruff check --fix . to autofix

afab status                     # live check against the tenant
afab status --transport rest    # same output, other backend — see below
afab modules                    # discovered modules; exits 1 on any module problem
afab logout                     # drop the stored sign-in

AFABRIC_MODULE_PATH=/some/dir afab modules   # load modules straight from directories
```

Run `ruff check . && pytest -q` before every commit.

**The repo lives on a 9p share** (`~/omarchy` is a symlink to `/mnt/mac`, mounted from
the Mac host). Python startup occasionally blocks for minutes in `p9_client_rpc` —
twice on 2026-09-10, once for `pytest`, once for `afab modules` — while the same command
finishes in seconds on the next run. Run anything that imports the venv in the
background and check the process state (`ps -o stat,wchan`) before assuming a hang is
in the code.

**Python floor is 3.11** (`StrEnum`, `ExceptionGroup`), ceiling is 3.12 because
`ms-fabric-cli` rejects 3.13+. The host system runs 3.14, so never invoke bare
`python` — it will be the wrong interpreter.

## Architecture

`docs/architecture.md` has the full contract; `docs/adr/0001-kernel-on-ms-mcp.md`
explains why the tool layer is not ours. The parts that only make sense across files:

**The kernel is use-case free.** `src/afabric/kernel/` knows tools, policy, journal
and the agent loop — nothing about workspaces, notebooks or pipelines. Anything
domain-specific belongs in a module under `src/afabric/modules/<name>/`, discovered
through the `afabric.modules` entry point group plus a directory scan. Adding a module
must never require touching the kernel; if it does, the abstraction is wrong.

**One `Change` type is load-bearing.** `model/change.py` is what lets policy, approval,
dry-run and the journal work identically for modules that do not exist yet. Policy
reasons about `Risk`, never about the action verb, so modules add verbs freely.
Resist adding a module-specific escape hatch here.

**Modules never import each other.** Cross-module dependencies are capabilities
declared in `manifest.py` (`provides`/`requires`) and resolved through
`ToolBus.resolve()` at runtime.

**Two transports, one signature.** `toolbus.py` dispatches to Core MCP (interactive,
browser/device sign-in) or plain REST (service principal, unattended). This split
exists only because Core MCP cannot authenticate unattended. Callers must never branch
on transport; `afab status --transport mcp` and `--transport rest` producing identical
output is the standing regression test.

**`kernel/tools.py` is the single place tool names live.** Core MCP is in preview and
its surface may change before GA. A rename is a one-line edit there and nowhere else.

## Fabric Core MCP behaviours worth not rediscovering

Found against the live server, absent from the docs, each one already handled:

- A missing `arguments` member is rejected outright — passing `None` yields
  "Invalid tool call params" even for tools that take no arguments. Always send `{}`.
- Responses are JSON followed by a `RequestId: …` line. `json.loads` fails on that and
  silently hands callers a string; `mcp_client.parse_payload` uses `raw_decode`.
- Arguments are PascalCase (`WorkspaceId`) where REST paths use camelCase. The
  `mcp_args` map in the catalog translates; modules only ever write camelCase.
- Both transports paginate. A tool missing `paged=True` truncates results **silently**,
  which is worse than an error.
- Write tools take their body nested under `Details` — except `create_workspace`, which
  takes it flat. `mcp_body` in the catalog records which; `_mcp_payload` does the shaping.
- Not every REST operation has an MCP tool: `assignToCapacity` has none. Such tools carry
  only `rest` and the ToolBus serves them over REST on either transport.
- `list_folders` omits `parentFolderId` entirely for folders at the top level rather than
  sending it as null, so "is a root folder" is tested by the field's *absence*.

## Authentication

Persisting a sign-in needs *two* things: a token cache **and** an `AuthenticationRecord`
handed back on the next credential construction. Without the record, azure-identity
re-prompts however warm the cache is. Record lives at
`~/.config/agentic-fabric/auth_record.json`.

Encrypted caching needs libsecret and a session keyring, which this headless VM lacks.
`AFABRIC_TOKEN_CACHE=auto` falls back to a plaintext cache with a warning; `none`
disables caching entirely.

## Error reporting

The MCP transport runs on anyio TaskGroups, so failures surface as
`ExceptionGroup: unhandled errors in a TaskGroup`, which says nothing. `cli._describe`
walks into groups and cause chains — keep new error paths going through it.

## Working style in this repo

Phases are defined in `docs/plan.md` — a local, gitignored file, so it is present on
the maintainer's machine but absent from a fresh clone. `TODO.md` is tracked and
carries the current pick-up point. Each phase ends with a commit, and only
after its verification actually ran — for tenant-facing work that means a live call,
not just green unit tests.

Fabric VS Code extension artifacts (`.work-folder-info`, UUID-named directories) appear
in the working tree and are gitignored. Leave them alone.
