"""Reconcile workspaces, root folders and role assignments.

Three functions, split by what they are allowed to touch:

- `observe(bus, desired)` — the only one doing I/O. Reads the tenant.
- `plan(desired, observed, policy)` — pure. Desired vs observed -> `list[Change]`.
- `project(observed, changes)` — pure. What the tenant would look like afterwards.

`plan` being pure is what lets the whole module be tested against recorded fixtures,
and `project` is what makes idempotency checkable: planning against the projected
state must yield nothing.

`apply(changes, bus)` executes a plan in order. It is only ever handed changes policy
has already let through.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from afabric.kernel.desired import PolicyConfig
from afabric.model.change import Change, Outcome, Risk
from afabric.modules.workspace.model import (
    Config,
    Observed,
    ObservedFolder,
    ObservedRole,
    ObservedWorkspace,
    WorkspaceSpec,
)

MODULE = "workspace"

# Fabric creates one of these per user. It cannot be deleted, shared or reassigned,
# so the reconciler neither manages nor prunes it.
PERSONAL = "Personal"


class PlanError(ValueError):
    """The desired state cannot be planned against this tenant at all."""


# --- observe ----------------------------------------------------------------------


async def observe(bus, desired: Config) -> Observed:
    workspaces = await bus.call("list_workspaces")
    capacities = await bus.call("list_capacities")
    declared = {w.name for w in desired}

    observed = Observed(
        identity=getattr(bus, "identity", None),
        capacities={c["displayName"]: c["id"] for c in _rows(capacities)},
    )

    for row in _rows(workspaces):
        workspace = ObservedWorkspace(
            id=row["id"],
            name=row["displayName"],
            description=row.get("description") or "",
            capacity_id=row.get("capacityId"),
            type=row.get("type", "Workspace"),
        )
        # Details cost two calls per workspace; only declared ones need them.
        if workspace.name in declared and workspace.type != PERSONAL:
            folders = await bus.call("list_folders", workspaceId=workspace.id)
            roles = await bus.call("list_workspace_roles", workspaceId=workspace.id)
            workspace.folders = [
                ObservedFolder(id=f["id"], name=f["displayName"])
                for f in _rows(folders)
                if not f.get("parentFolderId")
            ]
            workspace.roles = [
                ObservedRole(
                    assignment_id=r["id"],
                    principal=r["principal"]["id"],
                    type=r["principal"].get("type", "User"),
                    role=r["role"],
                )
                for r in _rows(roles)
            ]
            workspace.details_loaded = True
        observed.workspaces.append(workspace)

    return observed


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("value", [])
    return [row for row in payload or [] if isinstance(row, dict)]


# --- plan -------------------------------------------------------------------------


def plan(desired: Config, observed: Observed, policy: PolicyConfig) -> list[Change]:
    changes: list[Change] = []
    existing = observed.by_name()

    for spec in desired:
        capacity_id = _resolve_capacity(spec, observed)
        current = existing.get(spec.name)
        if current is None:
            changes.extend(_create(spec, capacity_id, observed.identity))
        else:
            changes.extend(_converge(spec, current, capacity_id, policy))

    if policy.prune:
        declared = {w.name for w in desired}
        for workspace in observed.workspaces:
            if workspace.name not in declared and workspace.type != PERSONAL:
                changes.append(
                    Change(
                        module=MODULE,
                        action="workspace.delete",
                        target=workspace.name,
                        risk=Risk.DESTRUCTIVE,
                        before=_workspace_state(workspace),
                        reason="not declared, and policy.prune is on",
                        metadata={"workspace_id": workspace.id},
                    )
                )

    return changes


def _resolve_capacity(spec: WorkspaceSpec, observed: Observed) -> str | None:
    if spec.capacity is None:
        return None
    try:
        return observed.capacities[spec.capacity]
    except KeyError:
        known = ", ".join(sorted(observed.capacities)) or "none visible"
        raise PlanError(
            f"workspace {spec.name!r}: capacity {spec.capacity!r} not found (known: {known})"
        ) from None


def _create(spec: WorkspaceSpec, capacity_id: str | None, identity: str | None) -> list[Change]:
    changes = [
        Change(
            module=MODULE,
            action="workspace.create",
            target=spec.name,
            risk=Risk.SAFE,
            after={
                "name": spec.name,
                "description": spec.description or "",
                "capacity_id": capacity_id,
            },
            reason="declared, not present",
            metadata={"workspace": spec.name},
        )
    ]
    changes.extend(_folder_create(spec.name, None, name) for name in spec.folders)
    # Fabric makes whoever creates a workspace its Admin. Granting that same principal
    # again fails, so the declaration is satisfied without planning it.
    changes.extend(
        _role_grant(spec.name, None, role)
        for role in spec.roles
        if identity is None or str(role.principal) != identity
    )
    return changes


def _converge(
    spec: WorkspaceSpec,
    current: ObservedWorkspace,
    capacity_id: str | None,
    policy: PolicyConfig,
) -> list[Change]:
    if current.type == PERSONAL:
        raise PlanError(f"workspace {spec.name!r} is a personal workspace and cannot be managed")
    if not current.details_loaded:
        # Comparing against lists that were never fetched would report every folder and
        # role as missing. Refuse rather than plan against a guess.
        raise PlanError(f"workspace {spec.name!r}: folders and roles were not observed")

    changes: list[Change] = []
    ws_id = current.id

    if spec.description is not None and spec.description != current.description:
        changes.append(
            Change(
                module=MODULE,
                action="workspace.update",
                target=spec.name,
                risk=Risk.REVERSIBLE,
                before={"description": current.description},
                after={"description": spec.description},
                reason="description differs",
                metadata={"workspace": spec.name, "workspace_id": ws_id},
            )
        )

    if capacity_id is not None and capacity_id != current.capacity_id:
        changes.append(
            Change(
                module=MODULE,
                action="workspace.assign_capacity",
                target=spec.name,
                risk=Risk.REVERSIBLE,
                before={"capacity_id": current.capacity_id},
                after={"capacity_id": capacity_id, "capacity": spec.capacity},
                reason="capacity differs",
                metadata={"workspace": spec.name, "workspace_id": ws_id},
            )
        )

    observed_folders = {f.name: f for f in current.folders}
    for name in spec.folders:
        if name not in observed_folders:
            changes.append(_folder_create(spec.name, ws_id, name))
    if policy.prune:
        for name, folder in observed_folders.items():
            if name not in spec.folders:
                changes.append(
                    Change(
                        module=MODULE,
                        action="folder.delete",
                        target=f"{spec.name}/{name}",
                        risk=Risk.DESTRUCTIVE,
                        before={"name": name},
                        reason="not declared, and policy.prune is on",
                        metadata={"workspace_id": ws_id, "folder_id": folder.id},
                    )
                )

    observed_roles = {r.principal: r for r in current.roles}
    declared_principals: set[UUID] = set()
    for role in spec.roles:
        declared_principals.add(role.principal)
        present = observed_roles.get(role.principal)
        if present is None:
            changes.append(_role_grant(spec.name, ws_id, role))
        elif present.role != role.role:
            changes.append(
                Change(
                    module=MODULE,
                    action="role.update",
                    target=f"{spec.name} ← {role.principal}",
                    risk=Risk.REVERSIBLE,
                    before={"role": present.role},
                    after={"role": role.role},
                    reason="role differs",
                    metadata={
                        "workspace_id": ws_id,
                        "assignment_id": present.assignment_id,
                        "principal": str(role.principal),
                    },
                )
            )
    if policy.prune:
        for principal, present in observed_roles.items():
            if principal not in declared_principals:
                changes.append(
                    Change(
                        module=MODULE,
                        action="role.revoke",
                        target=f"{spec.name} ← {principal}",
                        risk=Risk.DESTRUCTIVE,
                        before={"role": present.role, "type": present.type},
                        # Pruning roles can revoke the caller's own access. Approval
                        # in phase 3 is the guard; the reason makes the stakes visible.
                        reason="not declared, and policy.prune is on — "
                        "check this is not your own access",
                        metadata={
                            "workspace_id": ws_id,
                            "assignment_id": present.assignment_id,
                            "principal": str(principal),
                        },
                    )
                )

    return changes


def _folder_create(workspace: str, ws_id: str | None, name: str) -> Change:
    return Change(
        module=MODULE,
        action="folder.create",
        target=f"{workspace}/{name}",
        risk=Risk.SAFE,
        after={"name": name},
        reason="declared, not present",
        metadata={"workspace": workspace, "workspace_id": ws_id},
    )


def _role_grant(workspace: str, ws_id: str | None, role) -> Change:
    # Granting access destroys nothing, but a wrong grant is a security incident, not a
    # cosmetic one — so it is never SAFE.
    return Change(
        module=MODULE,
        action="role.grant",
        target=f"{workspace} ← {role.principal}",
        risk=Risk.REVERSIBLE,
        after={"role": role.role, "type": role.type},
        reason="declared, not present",
        metadata={"workspace": workspace, "workspace_id": ws_id, "principal": str(role.principal)},
    )


def _workspace_state(workspace: ObservedWorkspace) -> dict[str, Any]:
    return {
        "name": workspace.name,
        "description": workspace.description,
        "capacity_id": workspace.capacity_id,
    }


# --- project ----------------------------------------------------------------------


def project(observed: Observed, changes: list[Change]) -> Observed:
    """Apply changes to a copy of the observed state, without touching the tenant.

    Ids that only the tenant can assign are stood in for with `planned:` placeholders.
    """
    result = observed.model_copy(deep=True)

    for change in changes:
        if change.module != MODULE:
            continue
        action, after, meta = change.action, change.after or {}, change.metadata
        by_name = result.by_name()

        if action == "workspace.create":
            result.workspaces.append(
                ObservedWorkspace(
                    id=f"planned:{after['name']}",
                    name=after["name"],
                    description=after.get("description") or "",
                    capacity_id=after.get("capacity_id"),
                    details_loaded=True,
                )
            )
        elif action == "workspace.delete":
            result.workspaces = [w for w in result.workspaces if w.id != meta["workspace_id"]]
        elif action == "workspace.update":
            _find(result, meta).description = after["description"]
        elif action == "workspace.assign_capacity":
            _find(result, meta).capacity_id = after["capacity_id"]
        elif action == "folder.create":
            workspace = by_name.get(meta.get("workspace")) or _find(result, meta)
            workspace.folders.append(
                ObservedFolder(id=f"planned:{after['name']}", name=after["name"])
            )
        elif action == "folder.delete":
            workspace = _find(result, meta)
            workspace.folders = [f for f in workspace.folders if f.id != meta["folder_id"]]
        elif action == "role.grant":
            workspace = by_name.get(meta.get("workspace")) or _find(result, meta)
            workspace.roles.append(
                ObservedRole(
                    assignment_id=f"planned:{meta['principal']}",
                    principal=meta["principal"],
                    type=after["type"],
                    role=after["role"],
                )
            )
        elif action == "role.update":
            workspace = _find(result, meta)
            for role in workspace.roles:
                if role.assignment_id == meta["assignment_id"]:
                    role.role = after["role"]
        elif action == "role.revoke":
            workspace = _find(result, meta)
            workspace.roles = [
                r for r in workspace.roles if r.assignment_id != meta["assignment_id"]
            ]
        else:
            raise ValueError(f"project() does not know how to apply {action!r}")

    return result


def _find(observed: Observed, meta: dict[str, Any]) -> ObservedWorkspace:
    for workspace in observed.workspaces:
        if workspace.id == meta.get("workspace_id"):
            return workspace
    raise KeyError(f"no workspace with id {meta.get('workspace_id')!r} in the projected state")


# --- apply ------------------------------------------------------------------------


async def apply(changes: list[Change], bus) -> list[Outcome]:
    """Execute changes in plan order, stopping at the first failure.

    Folders and roles for a workspace created in the same run carry only its name; the id
    becomes known once `workspace.create` returns and is filled in from there. Stopping
    at the first failure is deliberate — later changes often depend on earlier ones, and
    a half-applied run is easier to reason about when it stops at a known point.
    """
    created: dict[str, str] = {}
    outcomes: list[Outcome] = []

    for change in changes:
        try:
            output = await _apply_one(change, bus, created)
        except Exception as exc:
            outcomes.append(Outcome(change=change, ok=False, detail=f"{type(exc).__name__}: {exc}"))
            break
        outcomes.append(Outcome(change=change, ok=True, output=output or {}))

    return outcomes


async def _apply_one(change: Change, bus, created: dict[str, str]) -> dict[str, Any] | None:
    action, after, meta = change.action, change.after or {}, change.metadata

    def workspace_id() -> str:
        ws_id = meta.get("workspace_id") or created.get(meta.get("workspace", ""))
        if not ws_id:
            raise RuntimeError(f"no id known for workspace {meta.get('workspace')!r}")
        return ws_id

    if action == "workspace.create":
        args = {"displayName": after["name"], "description": after.get("description") or ""}
        if after.get("capacity_id"):
            args["capacityId"] = after["capacity_id"]
        result = await bus.call("create_workspace", **args)
        created[after["name"]] = result["id"]
        return {"workspace_id": result["id"]}

    if action == "workspace.update":
        await bus.call(
            "update_workspace", workspaceId=workspace_id(), description=after["description"]
        )
    elif action == "workspace.assign_capacity":
        await bus.call(
            "assign_to_capacity", workspaceId=workspace_id(), capacityId=after["capacity_id"]
        )
    elif action == "workspace.delete":
        await bus.call("delete_workspace", workspaceId=workspace_id())
    elif action == "folder.create":
        result = await bus.call(
            "create_folder", workspaceId=workspace_id(), displayName=after["name"]
        )
        return {"folder_id": result.get("id")} if isinstance(result, dict) else None
    elif action == "folder.delete":
        await bus.call("delete_folder", workspaceId=workspace_id(), folderId=meta["folder_id"])
    elif action == "role.grant":
        await bus.call(
            "add_workspace_role",
            workspaceId=workspace_id(),
            principal={"id": meta["principal"], "type": after["type"]},
            role=after["role"],
        )
    elif action == "role.update":
        await bus.call(
            "update_workspace_role",
            workspaceId=workspace_id(),
            roleAssignmentId=meta["assignment_id"],
            role=after["role"],
        )
    elif action == "role.revoke":
        await bus.call(
            "delete_workspace_role",
            workspaceId=workspace_id(),
            roleAssignmentId=meta["assignment_id"],
        )
    else:
        raise ValueError(f"apply() does not know how to perform {action!r}")
    return None
