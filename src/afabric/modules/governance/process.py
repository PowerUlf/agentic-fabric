"""Audit workspaces and items against house rules, and plan what meeting them takes.

Same split as the other modules: `observe` does the I/O, `plan` and `project` are pure,
`apply` refuses. Every rule reads state the kernel catalog already serves, so this module
adds no tools of its own — a directory was enough.

A violation is planned as a `Change` like any other: `before` is what the tenant holds,
`after` what the rule demands. That is what lets policy, approval and the journal treat
an audit finding exactly like a workspace change.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from typing import Any

from afabric.kernel.desired import PolicyConfig
from afabric.model.change import Change, Outcome, Risk
from afabric.modules.governance.model import (
    DERIVED_TYPES,
    Config,
    Observed,
    ObservedItem,
    ObservedWorkspace,
)

MODULE = "governance"

# Fabric creates one per user; it has no description, capacity or role list to govern.
PERSONAL = "Personal"


class PlanError(ValueError):
    """The desired state cannot be planned against this tenant at all."""


# --- observe ----------------------------------------------------------------------


async def observe(bus, desired: Config) -> Observed:
    observed = Observed()
    if not desired.audits_anything:
        return observed

    for row in _rows(await bus.call("list_workspaces")):
        if row.get("type", "Workspace") == PERSONAL:
            continue
        if not fnmatchcase(row["displayName"], desired.scope):
            continue

        workspace = ObservedWorkspace(
            id=row["id"],
            name=row["displayName"],
            description=row.get("description") or "",
            capacity_id=row.get("capacityId"),
        )
        if desired.audits_items:
            workspace.items = [
                ObservedItem(
                    id=item["id"],
                    name=item["displayName"],
                    type=item.get("type", ""),
                    description=item.get("description") or "",
                )
                for item in _rows(await bus.call("list_items", workspaceId=workspace.id))
                if item.get("type") not in DERIVED_TYPES
            ]
            workspace.items_loaded = True
        if desired.min_admins is not None:
            roles = _rows(await bus.call("list_workspace_roles", workspaceId=workspace.id))
            workspace.admins = sum(1 for role in roles if role.get("role") == "Admin")
            workspace.roles_loaded = True

        observed.workspaces.append(workspace)

    return observed


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("value", [])
    return [row for row in payload or [] if isinstance(row, dict)]


# --- plan -------------------------------------------------------------------------


def plan(desired: Config, observed: Observed, policy: PolicyConfig) -> list[Change]:
    changes: list[Change] = []
    naming = re.compile(desired.item_naming) if desired.item_naming else None

    for workspace in observed.workspaces:
        if desired.workspace_description and not workspace.description:
            changes.append(
                _finding(
                    "governance.describe",
                    workspace.name,
                    Risk.SAFE,
                    before={"description": ""},
                    after={"description": "required by governance.workspace_description"},
                    reason="workspace has no description",
                    metadata={"workspace_id": workspace.id},
                )
            )

        if desired.capacity_required and not workspace.capacity_id:
            changes.append(
                _finding(
                    "governance.assign_capacity",
                    workspace.name,
                    Risk.SAFE,
                    before={"capacity_id": None},
                    after={"capacity_id": "required by governance.capacity_required"},
                    reason="workspace sits on no capacity",
                    metadata={"workspace_id": workspace.id},
                )
            )

        if desired.min_admins is not None:
            if not workspace.roles_loaded:
                raise PlanError(f"workspace {workspace.name!r}: role assignments were not observed")
            if workspace.admins < desired.min_admins:
                changes.append(
                    _finding(
                        "governance.grant_admin",
                        workspace.name,
                        Risk.SAFE,
                        before={"admins": workspace.admins},
                        after={"admins": desired.min_admins},
                        reason=f"{workspace.admins} admin(s), governance.min_admins is "
                        f"{desired.min_admins}",
                        metadata={"workspace_id": workspace.id},
                    )
                )

        changes.extend(_items(desired, workspace, naming))

    return changes


def _items(desired: Config, workspace: ObservedWorkspace, naming) -> list[Change]:
    if not desired.audits_items:
        return []
    if not workspace.items_loaded:
        # Auditing a list nobody fetched would report every item as compliant.
        raise PlanError(f"workspace {workspace.name!r}: items were not observed")

    changes: list[Change] = []
    for item in workspace.items:
        target = f"{workspace.name}/{item.name}"
        metadata = {"workspace_id": workspace.id, "item_id": item.id, "item_type": item.type}

        if item.type in desired.item_description and not item.description:
            changes.append(
                _finding(
                    "governance.describe",
                    target,
                    Risk.SAFE,
                    before={"description": ""},
                    after={"description": "required by governance.item_description"},
                    reason=f"{item.type} has no description",
                    metadata=metadata,
                )
            )

        if naming is not None and not naming.match(item.name):
            changes.append(
                _finding(
                    "governance.rename",
                    target,
                    # Renaming breaks whatever references the old name.
                    Risk.REVERSIBLE,
                    before={"name": item.name},
                    after={"name": f"must match {desired.item_naming}"},
                    reason=f"name does not match governance.item_naming "
                    f"({desired.item_naming})",
                    metadata=metadata,
                )
            )

    return changes


def _finding(action: str, target: str, risk: Risk, **fields) -> Change:
    return Change(module=MODULE, action=action, target=target, risk=risk, **fields)


# --- project ----------------------------------------------------------------------


def project(observed: Observed, changes: list[Change]) -> Observed:
    """What the tenant would look like once every finding is addressed."""
    after = observed.model_copy(deep=True)
    workspaces = {w.id: w for w in after.workspaces}
    items = {(w.id, i.id): i for w in after.workspaces for i in w.items}

    for change in changes:
        workspace = workspaces.get(change.metadata.get("workspace_id"))
        if workspace is None:
            continue
        item = items.get((workspace.id, change.metadata.get("item_id")))

        if change.action == "governance.describe":
            target = item if item is not None else workspace
            target.description = "described"
        elif change.action == "governance.assign_capacity":
            workspace.capacity_id = "projected-capacity"
        elif change.action == "governance.grant_admin":
            workspace.admins = int(change.after["admins"])
        elif change.action == "governance.rename" and item is not None:
            item.name = f"renamed_{item.id[:8]}"

    return after


# --- apply ------------------------------------------------------------------------


async def apply(changes: list[Change], bus) -> list[Outcome]:
    """Not implemented: this module audits.

    Every finding needs a decision this module cannot make — which description, which
    capacity, which principal, which name. Planning states the gap; closing it belongs
    in `workspaces:` where the value is declared.
    """
    return [
        Outcome(
            change=change,
            ok=False,
            detail="governance audits only; declare the fix in workspaces: and apply that",
        )
        for change in changes[:1]
    ]
