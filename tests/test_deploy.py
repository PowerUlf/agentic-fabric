"""Phase 5: the fourth module — promoting item definitions, compared rather than published."""

import asyncio

import pytest
from pydantic import ValidationError

from afabric.kernel.desired import PolicyConfig
from afabric.modules.deploy import process
from afabric.modules.deploy.model import Config, digest

POLICY = PolicyConfig()
PRUNING = PolicyConfig(prune=True)

WORKSPACES = [
    {"id": "dev", "displayName": "faf_dev"},
    {"id": "test", "displayName": "afab_e2e"},
    {"id": "mine", "displayName": "My workspace", "type": "Personal"},
]


def _definition(code: str, *, name: str = "whatever"):
    """A definition the way Fabric returns one: parts in no particular order."""
    return {
        "definition": {
            "parts": [
                {"path": ".platform", "payload": f"meta-{name}", "payloadType": "InlineBase64"},
                {"path": "notebook-content.py", "payload": code, "payloadType": "InlineBase64"},
            ]
        }
    }


class FakeBus:
    def __init__(self, items, definitions):
        self.items, self.definitions = items, definitions
        self.calls = []

    async def call(self, name, **args):
        self.calls.append((name, args))
        if name == "list_workspaces":
            return WORKSPACES
        if name == "list_items":
            return self.items[args["workspaceId"]]
        if name == "get_item_definition":
            return self.definitions[args["itemId"]]
        raise AssertionError(f"unexpected tool {name}")


def _item(id_, name, type_="Notebook"):
    return {"id": id_, "displayName": name, "type": type_}


def _bus(dev_items, test_items, definitions):
    return FakeBus({"dev": dev_items, "test": test_items}, definitions)


def _run(bus, **overrides):
    spec = {"source": "faf_dev", "target": "afab_e2e"}
    spec.update(overrides)
    desired = Config.model_validate([spec])
    return desired, asyncio.run(process.observe(bus, desired))


# --- the declaration --------------------------------------------------------------


def test_source_and_target_must_differ():
    with pytest.raises(ValidationError, match="same workspace"):
        Config.model_validate([{"source": "faf_dev", "target": "faf_dev"}])


def test_derived_types_are_refused():
    with pytest.raises(ValidationError, match="follows its parent"):
        Config.model_validate(
            [{"source": "faf_dev", "target": "afab_e2e", "types": ["SemanticModel"]}]
        )


# --- the fingerprint --------------------------------------------------------------


class TestDigest:
    def test_platform_metadata_is_ignored(self):
        here = _definition("code", name="nb_a")["definition"]
        there = _definition("code", name="nb_b")["definition"]
        assert digest(here) == digest(there)

    def test_part_order_does_not_matter(self):
        one = {"parts": [{"path": "a", "payload": "x"}, {"path": "b", "payload": "y"}]}
        other = {"parts": [{"path": "b", "payload": "y"}, {"path": "a", "payload": "x"}]}
        assert digest(one) == digest(other)

    def test_content_changes_the_fingerprint(self):
        assert digest(_definition("one")["definition"]) != digest(
            _definition("two")["definition"]
        )

    def test_nothing_to_hash_is_none(self):
        assert digest(None) is None
        assert digest({"parts": [{"path": ".platform", "payload": "meta"}]}) is None


# --- plan -------------------------------------------------------------------------


def test_missing_items_are_planned_as_creations():
    bus = _bus([_item("d1", "nb_bronze")], [], {"d1": _definition("code")})
    desired, observed = _run(bus)

    changes = process.plan(desired, observed, POLICY)

    assert [(c.action, c.target) for c in changes] == [("item.create", "afab_e2e/nb_bronze")]
    assert changes[0].risk.value == "safe"


def test_a_differing_definition_is_planned_as_an_update():
    bus = _bus(
        [_item("d1", "nb_bronze")],
        [_item("t1", "nb_bronze")],
        {"d1": _definition("new"), "t1": _definition("old")},
    )
    desired, observed = _run(bus)

    [change] = process.plan(desired, observed, POLICY)

    assert change.action == "item.update" and change.risk.value == "reversible"
    assert change.metadata["target_item_id"] == "t1"


def test_an_identical_definition_is_no_change():
    bus = _bus(
        [_item("d1", "nb_bronze")],
        [_item("t1", "nb_bronze")],
        {"d1": _definition("same", name="dev"), "t1": _definition("same", name="test")},
    )
    desired, observed = _run(bus)
    assert process.plan(desired, observed, POLICY) == []


def test_same_name_different_type_is_a_different_item():
    bus = _bus(
        [_item("d1", "load", "DataPipeline")],
        [_item("t1", "load", "Notebook")],
        {"d1": _definition("a"), "t1": _definition("a")},
    )
    desired, observed = _run(bus)

    [change] = process.plan(desired, observed, POLICY)
    assert change.action == "item.create"


def test_extra_items_in_the_target_only_go_with_prune():
    bus = _bus([], [_item("t1", "nb_old")], {"t1": _definition("old")})
    desired, observed = _run(bus)

    assert process.plan(desired, observed, POLICY) == []

    [change] = process.plan(desired, observed, PRUNING)
    assert change.action == "item.delete" and change.risk.value == "destructive"


def test_types_and_glob_narrow_what_is_compared():
    bus = _bus(
        [_item("d1", "nb_bronze"), _item("d2", "nb_gold"), _item("d3", "pl_load", "DataPipeline")],
        [],
        {k: _definition("code") for k in ("d1", "d2", "d3")},
    )
    desired, observed = _run(bus, items="nb_b*", types=["Notebook"])

    assert [c.target for c in process.plan(desired, observed, POLICY)] == ["afab_e2e/nb_bronze"]
    # Only the matching item's definition was fetched; the others were never asked for.
    assert [args["itemId"] for name, args in bus.calls if name == "get_item_definition"] == ["d1"]


def test_a_missing_workspace_refuses_planning():
    bus = _bus([], [], {})
    desired, observed = _run(bus, target="nope")
    assert observed.missing_workspaces == ["nope"]
    with pytest.raises(process.PlanError, match="nope"):
        process.plan(desired, observed, POLICY)


# --- project ----------------------------------------------------------------------


def test_planning_against_the_projection_is_empty():
    bus = _bus(
        [_item("d1", "nb_new"), _item("d2", "nb_changed")],
        [_item("t2", "nb_changed"), _item("t3", "nb_extra")],
        {
            "d1": _definition("new"),
            "d2": _definition("fresh"),
            "t2": _definition("stale"),
            "t3": _definition("gone"),
        },
    )
    desired, observed = _run(bus)

    changes = process.plan(desired, observed, PRUNING)
    assert {c.action for c in changes} == {"item.create", "item.update", "item.delete"}

    after = process.project(observed, changes)
    assert process.plan(desired, after, PRUNING) == []
    assert len(observed.pairs[0].target_items) == 2


# --- apply ------------------------------------------------------------------------


def test_apply_refuses_and_says_why():
    bus = _bus([_item("d1", "nb_bronze")], [], {"d1": _definition("code")})
    desired, observed = _run(bus)
    changes = process.plan(desired, observed, POLICY)

    outcomes = asyncio.run(process.apply(changes, bus))

    assert len(outcomes) == 1 and not outcomes[0].ok
    assert "not implemented" in outcomes[0].detail
