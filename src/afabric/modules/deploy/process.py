"""Compare item definitions between two workspaces, and promote them.

Same split as the other modules: `observe` does the I/O, `plan` and `project` are pure,
`apply` writes. The comparison is what makes a dry run possible at all — `fabric-cicd`
publishes rather than previews, so a plan built on it would have to guess.

Two items are the same when their definitions hash the same, with `.platform` left out:
it holds the display name, logical id and home workspace, which differ between two
workspaces by construction.

Promoting rewrites the ids inside a definition. A notebook holds the workspace and the
lakehouse it attaches to; a pipeline holds the items it invokes. Left alone, the copy
reaches back into the workspace it came from — so an id that cannot be resolved in the
target blocks that item rather than travelling as it is.
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
    digest_parts,
    references,
    rewrite,
)

MODULE = "deploy"
PERSONAL = "Personal"


class PlanError(ValueError):
    """The desired state cannot be planned against this tenant at all."""


def _key(name: str, item_type: str) -> str:
    """Identity of an item across workspaces. Ids never match; name and type do."""
    return f"{name}\x00{item_type}"


def _readable(key: str) -> str:
    name, _, item_type = key.partition("\x00")
    return f"{name} ({item_type})"


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
        source_rows = _rows(await bus.call("list_items", workspaceId=pair.source_id))
        target_rows = _rows(await bus.call("list_items", workspaceId=pair.target_id))

        # Every item, not just the promoted ones: a notebook may reference a lakehouse
        # nobody is promoting, and naming that reference needs the whole inventory.
        pair.source_all = {r["id"]: _key(r["displayName"], r.get("type", "")) for r in source_rows}
        pair.target_all = {_key(r["displayName"], r.get("type", "")): r["id"] for r in target_rows}

        pair.source_items = await _items(bus, pair.source_id, source_rows, promotion)
        pair.target_items = await _items(bus, pair.target_id, target_rows, promotion)
        _fingerprint(pair, promotion)
        observed.pairs.append(pair)

    return observed


def _fingerprint(pair: ObservedPair, promotion: PromotionSpec) -> None:
    """Fingerprint both sides with their ids replaced by what the ids *mean*.

    A promoted copy carries the target's ids, so hashing the definitions as they stand
    would report every promoted item as different from its source, for ever. Replacing
    each known id with a token — `@workspace`, `@nb_bronze (Notebook)` — makes the two
    sides comparable. Ids neither side can name stay as they are: if those differ, that
    is a real difference.
    """
    source_tokens = {pair.source_id: "@workspace"}
    source_tokens.update({i: f"@{key}" for i, key in pair.source_all.items()})

    # The target's name for a source item may differ, so the mapping is read backwards.
    back = {target: source for source, target in promotion.map.items()}
    target_tokens = {pair.target_id: "@workspace"}
    for key, item_id in pair.target_all.items():
        name, _, item_type = key.partition("\x00")
        target_tokens[item_id] = f"@{_key(back.get(name, name), item_type)}"

    for item in pair.source_items:
        item.digest = digest_parts(rewrite({"parts": item.parts}, source_tokens))
    for item in pair.target_items:
        item.digest = digest_parts(rewrite({"parts": item.parts}, target_tokens))


async def _items(bus, workspace_id: str, rows: list[dict], promotion: PromotionSpec):
    found: list[ObservedItem] = []
    for row in rows:
        if row.get("type") not in promotion.types:
            continue
        if not fnmatchcase(row["displayName"], promotion.items):
            continue
        # getDefinition answers 202; the ToolBus waits the operation out for us.
        body = await bus.call("get_item_definition", workspaceId=workspace_id, itemId=row["id"])
        definition = (body or {}).get("definition")
        found.append(
            ObservedItem(
                id=row["id"],
                name=row["displayName"],
                type=row["type"],
                digest=digest(definition),
                references=references(definition),
                parts=(definition or {}).get("parts", []),
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
    for promotion, pair in zip(desired, observed.pairs, strict=False):
        changes.extend(_promotion(promotion, pair, policy))
    return changes


def _promotion(promotion: PromotionSpec, pair: ObservedPair, policy: PolicyConfig):
    target_by_key = {_key(i.name, i.type): i for i in pair.target_items}
    resolved = _resolution(promotion, pair)
    # Items this run creates: their ids exist only once apply has made them.
    pending = {
        item.id: _key(item.name, item.type)
        for item in pair.source_items
        if _key(item.name, item.type) not in target_by_key
    }

    promotable: list[tuple[ObservedItem, Change]] = []
    blocked: list[Change] = []
    # An item whose dependency is blocked cannot travel either: promoting it would leave
    # it pointing at an id that never appears in the target. Settle that before planning.
    unpromotable = _unpromotable(pair, resolved, pending)

    for item in pair.source_items:
        present = target_by_key.get(_key(item.name, item.type))
        # Both digests were taken with ids replaced by what they mean, so a promoted
        # copy matches its source rather than differing by construction.
        if present is not None and present.digest == item.digest:
            continue

        unknown = (item.references - set(resolved) - set(pending)) | (
            item.references & unpromotable
        )
        metadata = {
            "source_workspace_id": pair.source_id,
            "target_workspace_id": pair.target_id,
            "source_item_id": item.id,
            "item_type": item.type,
            "name": item.name,
        }
        if unknown:
            blocked.append(
                Change(
                    module=MODULE,
                    action="item.blocked",
                    target=f"{pair.target}/{item.name}",
                    risk=Risk.SAFE,
                    before={
                        "references": sorted(
                            _readable(pair.source_all[ref]) if ref in pair.source_all else ref
                            for ref in unknown
                        )
                    },
                    reason="its definition points at something the target does not have; "
                    "promote it too, or name its counterpart under `map:`",
                    metadata=metadata,
                )
            )
            continue

        wanted = {ref: resolved[ref] for ref in item.references if ref in resolved}
        waits_for = {ref: pending[ref] for ref in item.references if ref in pending}
        if present is None:
            change = Change(
                module=MODULE,
                action="item.create",
                target=f"{pair.target}/{item.name}",
                risk=Risk.SAFE,
                after={"type": item.type, "from": f"{pair.source}/{item.name}"},
                reason="declared for promotion, not present in the target",
                metadata={
                    **metadata,
                    "replacements": wanted,
                    "pending": waits_for,
                    "promoted_digest": item.digest,
                },
            )
        else:
            change = Change(
                module=MODULE,
                action="item.update",
                target=f"{pair.target}/{item.name}",
                # The target's definition is overwritten, and `before` holds only its
                # fingerprint — not enough to put it back.
                risk=Risk.REVERSIBLE,
                before={"digest": present.digest},
                after={"digest": item.digest},
                reason="definition differs from the source",
                metadata={
                    **metadata,
                    "target_item_id": present.id,
                    "replacements": wanted,
                    "pending": waits_for,
                    "promoted_digest": item.digest,
                },
            )
        promotable.append((item, change))

    changes = _in_dependency_order(promotable)

    if policy.prune:
        declared = {_key(i.name, i.type) for i in pair.source_items}
        for item in pair.target_items:
            if _key(item.name, item.type) not in declared:
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

    # Blocked last: everything that can be promoted is attempted before the run stops.
    return changes + blocked


def _unpromotable(
    pair: ObservedPair, resolved: dict[str, str], pending: dict[str, str]
) -> set[str]:
    """Source ids that cannot reach the target, directly or through what they depend on."""
    blocked = {
        item.id
        for item in pair.source_items
        if item.references - set(resolved) - set(pending)
    }
    growing = True
    while growing:
        growing = False
        for item in pair.source_items:
            if item.id not in blocked and item.references & blocked:
                blocked.add(item.id)
                growing = True
    return blocked


def _resolution(promotion: PromotionSpec, pair: ObservedPair) -> dict[str, str]:
    """Source id -> target id, for everything the target already holds."""
    resolved = {pair.source_id: pair.target_id}
    for source_id, key in pair.source_all.items():
        name, _, item_type = key.partition("\x00")
        wanted = _key(promotion.map.get(name, name), item_type)
        if wanted in pair.target_all:
            resolved[source_id] = pair.target_all[wanted]
    return resolved


def _in_dependency_order(promotable: list[tuple[ObservedItem, Change]]) -> list[Change]:
    """An item that invokes another comes after it, so the id it needs exists.

    A cycle cannot be ordered; those items keep their original order rather than being
    dropped, and apply reports the missing id if one really is missing.
    """
    remaining = list(promotable)
    placed: set[str] = set()
    ordered: list[Change] = []

    while remaining:
        ready = [
            (item, change)
            for item, change in remaining
            if not (set(change.metadata["pending"]) - placed)
        ]
        if not ready:
            ready = remaining[:1]
        for item, change in ready:
            ordered.append(change)
            placed.add(item.id)
        remaining = [entry for entry in remaining if entry not in ready]

    return ordered


# --- project ----------------------------------------------------------------------


def project(observed: Observed, changes: list[Change]) -> Observed:
    """What the target would hold once every change succeeded."""
    after = observed.model_copy(deep=True)
    pairs = {p.target_id: p for p in after.pairs}

    for change in changes:
        pair = pairs.get(change.metadata.get("target_workspace_id"))
        if pair is None:
            continue
        name = change.metadata.get("name") or change.target.split("/", 1)[-1]
        item_type = change.metadata.get("item_type", "")
        source = next(
            (i for i in pair.source_items if (i.name, i.type) == (name, item_type)), None
        )

        # The fingerprint the copy will carry, ids rewritten — decided while planning.
        promoted = change.metadata.get("promoted_digest")
        if change.action == "item.create" and source is not None:
            pair.target_items.append(
                ObservedItem(
                    id=f"projected-{source.id}",
                    name=source.name,
                    type=source.type,
                    digest=promoted,
                )
            )
            pair.target_all[_key(source.name, source.type)] = f"projected-{source.id}"
        elif change.action == "item.update" and source is not None:
            for item in pair.target_items:
                if (item.name, item.type) == (name, item_type):
                    item.digest = promoted
        elif change.action == "item.delete":
            pair.target_items = [
                i for i in pair.target_items if (i.name, i.type) != (name, item_type)
            ]
            pair.target_all.pop(_key(name, item_type), None)

    return after


# --- apply ------------------------------------------------------------------------


async def apply(changes: list[Change], bus) -> list[Outcome]:
    """Promote in plan order, rewriting ids as items come into being.

    An item created here gets its target id only now, so the replacement map grows as
    the run proceeds — which is why plan put dependants after what they depend on.
    """
    created: dict[str, str] = {}
    outcomes: list[Outcome] = []

    for change in changes:
        try:
            outcomes.append(await _apply_one(change, bus, created))
        except Exception as exc:
            outcomes.append(
                Outcome(change=change, ok=False, detail=f"{type(exc).__name__}: {exc}")
            )
        if not outcomes[-1].ok:
            break

    return outcomes


async def _apply_one(change: Change, bus, created: dict[str, str]) -> Outcome:
    if change.action == "item.blocked":
        return Outcome(
            change=change,
            ok=False,
            detail="unresolved reference; promote what it points at, or map it",
        )

    if change.action == "item.delete":
        await bus.call(
            "delete_item",
            workspaceId=change.metadata["target_workspace_id"],
            itemId=change.metadata["target_item_id"],
        )
        return Outcome(change=change, ok=True, output={})

    replacements = dict(change.metadata["replacements"])
    for source_id, key in change.metadata["pending"].items():
        if source_id not in created:
            return Outcome(
                change=change,
                ok=False,
                detail=f"{_readable(key)} should have been promoted first but has no id yet",
            )
        replacements[source_id] = created[source_id]

    body = await bus.call(
        "get_item_definition",
        workspaceId=change.metadata["source_workspace_id"],
        itemId=change.metadata["source_item_id"],
    )
    definition = (body or {}).get("definition")
    if not definition:
        return Outcome(change=change, ok=False, detail="the source item has no definition")
    parts = rewrite(definition, replacements)

    if change.action == "item.create":
        result = await bus.call(
            "create_item_with_definition",
            workspaceId=change.metadata["target_workspace_id"],
            displayName=change.metadata["name"],
            type=change.metadata["item_type"],
            definition={"parts": parts},
        )
        new_id = (result or {}).get("id")
        if new_id:
            created[change.metadata["source_item_id"]] = new_id
        return Outcome(change=change, ok=True, output={"item_id": new_id})

    if change.action == "item.update":
        await bus.call(
            "update_item_definition",
            workspaceId=change.metadata["target_workspace_id"],
            itemId=change.metadata["target_item_id"],
            definition={"parts": parts},
        )
        created[change.metadata["source_item_id"]] = change.metadata["target_item_id"]
        return Outcome(change=change, ok=True, output={})

    return Outcome(change=change, ok=False, detail=f"unknown action {change.action!r}")
