"""Compare item definitions between two workspaces, and plan the promotion.

Same split as the other modules: `observe` does the I/O, `plan` and `project` are pure,
`apply` refuses. The comparison is what makes a dry run possible at all — `fabric-cicd`
publishes rather than previews, so a plan built on it would have to guess.

Two items are the same when their definitions hash the same, with `.platform` left out:
it holds the display name, logical id and home workspace, which differ between two
workspaces by construction.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Any

from afabric.kernel.desired import PolicyConfig
from afabric.model.change import Change, Outcome, Risk
from afabric.modules.deploy.model import (
    Config,
    Observed,
    ObservedItem,
    ObservedPair,
    PromotionSpec,
    digest,
)

MODULE = "deploy"
PERSONAL = "Personal"


class PlanError(ValueError):
    """The desired state cannot be planned against this tenant at all."""


# --- observe ----------------------------------------------------------------------


async def observe(bus, desired: Config) -> Observed:
    workspaces = {
        w["displayName"]: w["id"]
        for w in _rows(await bus.call("list_workspaces"))
        if w.get("type", "Workspace") != PERSONAL
    }
    observed = Observed()

    for promotion in desired:
        missing = [n for n in (promotion.source, promotion.target) if n not in workspaces]
        if missing:
            observed.missing_workspaces.extend(missing)
            continue

        pair = ObservedPair(
            source=promotion.source,
            source_id=workspaces[promotion.source],
            target=promotion.target,
            target_id=workspaces[promotion.target],
        )
        pair.source_items = await _items(bus, pair.source_id, promotion)
        pair.target_items = await _items(bus, pair.target_id, promotion)
        observed.pairs.append(pair)

    return observed


async def _items(bus, workspace_id: str, promotion: PromotionSpec) -> list[ObservedItem]:
    found: list[ObservedItem] = []
    for row in _rows(await bus.call("list_items", workspaceId=workspace_id)):
        if row.get("type") not in promotion.types:
            continue
        if not fnmatchcase(row["displayName"], promotion.items):
            continue
        # getDefinition answers 202; the ToolBus waits the operation out for us.
        body = await bus.call("get_item_definition", workspaceId=workspace_id, itemId=row["id"])
        found.append(
            ObservedItem(
                id=row["id"],
                name=row["displayName"],
                type=row["type"],
                digest=digest((body or {}).get("definition")),
            )
        )
    return found


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("value", [])
    return [row for row in payload or [] if isinstance(row, dict)]


# --- plan -------------------------------------------------------------------------


def plan(desired: Config, observed: Observed, policy: PolicyConfig) -> list[Change]:
    if observed.missing_workspaces:
        raise PlanError(
            "workspace(s) not found: " + ", ".join(sorted(set(observed.missing_workspaces)))
        )

    changes: list[Change] = []
    for pair in observed.pairs:
        # Name and type together identify an item across workspaces; ids never match.
        target = {(i.name, i.type): i for i in pair.target_items}

        for item in pair.source_items:
            present = target.get((item.name, item.type))
            metadata = {
                "source_workspace_id": pair.source_id,
                "target_workspace_id": pair.target_id,
                "source_item_id": item.id,
                "item_type": item.type,
            }
            if present is None:
                changes.append(
                    Change(
                        module=MODULE,
                        action="item.create",
                        target=f"{pair.target}/{item.name}",
                        risk=Risk.SAFE,
                        after={"type": item.type, "from": f"{pair.source}/{item.name}"},
                        reason="declared for promotion, not present in the target",
                        metadata=metadata,
                    )
                )
            elif present.digest != item.digest:
                changes.append(
                    Change(
                        module=MODULE,
                        action="item.update",
                        target=f"{pair.target}/{item.name}",
                        # The target's current definition is overwritten, and `before`
                        # holds only its fingerprint — not enough to put it back.
                        risk=Risk.REVERSIBLE,
                        before={"digest": present.digest},
                        after={"digest": item.digest},
                        reason="definition differs from the source",
                        metadata={**metadata, "target_item_id": present.id},
                    )
                )

        if policy.prune:
            declared = {(i.name, i.type) for i in pair.source_items}
            for item in pair.target_items:
                if (item.name, item.type) not in declared:
                    changes.append(
                        Change(
                            module=MODULE,
                            action="item.delete",
                            target=f"{pair.target}/{item.name}",
                            risk=Risk.DESTRUCTIVE,
                            before={"type": item.type, "digest": item.digest},
                            reason="not in the source, and policy.prune is on",
                            metadata={
                                "target_workspace_id": pair.target_id,
                                "target_item_id": item.id,
                                "item_type": item.type,
                            },
                        )
                    )

    return changes


# --- project ----------------------------------------------------------------------


def project(observed: Observed, changes: list[Change]) -> Observed:
    """What the target would hold once every change succeeded."""
    after = observed.model_copy(deep=True)
    pairs = {p.target_id: p for p in after.pairs}

    for change in changes:
        pair = pairs.get(change.metadata.get("target_workspace_id"))
        if pair is None:
            continue
        name = change.target.split("/", 1)[-1]
        item_type = change.metadata.get("item_type", "")
        source = next(
            (i for i in pair.source_items if (i.name, i.type) == (name, item_type)), None
        )

        if change.action == "item.create" and source is not None:
            pair.target_items.append(
                ObservedItem(
                    id=f"projected-{source.id}",
                    name=source.name,
                    type=source.type,
                    digest=source.digest,
                )
            )
        elif change.action == "item.update" and source is not None:
            for item in pair.target_items:
                if (item.name, item.type) == (name, item_type):
                    item.digest = source.digest
        elif change.action == "item.delete":
            pair.target_items = [
                i for i in pair.target_items if (i.name, i.type) != (name, item_type)
            ]

    return after


# --- apply ------------------------------------------------------------------------


async def apply(changes: list[Change], bus) -> list[Outcome]:
    """Not implemented: this module compares.

    Creating an item from a foreign definition means rewriting the ids inside it — the
    lakehouse a notebook attaches to differs per workspace — and that rewriting is the
    part `fabric-cicd` does with parameter files. Planning the diff is honest; writing
    it without that step would not be.
    """
    return [
        Outcome(
            change=change,
            ok=False,
            detail="deploy compares only; promoting an item is not implemented yet",
        )
        for change in changes[:1]
    ]
