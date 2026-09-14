"""Phase 5: promoting item definitions — compared first, then rewritten and written."""

import asyncio
import base64

import pytest
from pydantic import ValidationError

from afabric.kernel.desired import PolicyConfig
from afabric.modules.deploy import process
from afabric.modules.deploy.model import Config, digest, references, rewrite

POLICY = PolicyConfig()
PRUNING = PolicyConfig(prune=True)

SOURCE = "aaaaaaaa-0000-0000-0000-000000000000"
TARGET = "bbbbbbbb-0000-0000-0000-000000000000"
LAKE_SOURCE = "cccccccc-0000-0000-0000-000000000000"
LAKE_TARGET = "dddddddd-0000-0000-0000-000000000000"
NB_SOURCE = "eeeeeeee-0000-0000-0000-000000000000"
PL_SOURCE = "ffffffff-0000-0000-0000-000000000000"

WORKSPACES = [
    {"id": SOURCE, "displayName": "faf_dev"},
    {"id": TARGET, "displayName": "afab_e2e"},
    {"id": "mine", "displayName": "My workspace", "type": "Personal"},
]


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _definition(code: str, *, name: str = "whatever"):
    """A definition the way Fabric returns one: parts in no particular order."""
    return {
        "definition": {
            "parts": [
                {
                    "path": ".platform",
                    "payload": _b64(f"meta-{name}"),
                    "payloadType": "InlineBase64",
                },
                {
                    "path": "notebook-content.py",
                    "payload": _b64(code),
                    "payloadType": "InlineBase64",
                },
            ]
        }
    }


def _item(id_, name, type_="Notebook"):
    return {"id": id_, "displayName": name, "type": type_}


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
        if name == "create_item_with_definition":
            return {"id": f"new-{args['displayName']}"}
        if name in {"update_item_definition", "delete_item"}:
            return None
        raise AssertionError(f"unexpected tool {name}")


def _bus(source_items, target_items, definitions):
    return FakeBus({SOURCE: source_items, TARGET: target_items}, definitions)


def _run(bus, **overrides):
    spec = {"source": "faf_dev", "target": "afab_e2e"}
    spec.update(overrides)
    desired = Config.model_validate([spec])
    return desired, asyncio.run(process.observe(bus, desired))


def _plan(bus, policy=POLICY, **overrides):
    desired, observed = _run(bus, **overrides)
    return desired, observed, process.plan(desired, observed, policy)


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
        assert digest(_definition("one")["definition"]) != digest(_definition("two")["definition"])

    def test_nothing_to_hash_is_none(self):
        assert digest(None) is None
        assert digest({"parts": [{"path": ".platform", "payload": "meta"}]}) is None


# --- references -------------------------------------------------------------------


class TestReferences:
    def test_ids_are_read_out_of_the_parts(self):
        found = references(_definition(f"attach {SOURCE} and {LAKE_SOURCE}")["definition"])
        assert found == {SOURCE, LAKE_SOURCE}

    def test_platform_metadata_and_the_empty_guid_are_not_references(self):
        definition = {
            "parts": [
                {"path": ".platform", "payload": _b64(f"id {SOURCE}")},
                {"path": "x", "payload": _b64("logicalId 00000000-0000-0000-0000-000000000000")},
            ]
        }
        assert references(definition) == set()

    def test_rewrite_swaps_ids_and_drops_platform(self):
        [part] = rewrite(_definition(f"lakehouse {LAKE_SOURCE}")["definition"], {
            LAKE_SOURCE: LAKE_TARGET
        })
        assert part["path"] == "notebook-content.py"
        assert base64.b64decode(part["payload"]).decode() == f"lakehouse {LAKE_TARGET}"


# --- plan -------------------------------------------------------------------------


def test_missing_items_are_planned_as_creations():
    bus = _bus([_item("d1", "nb_bronze")], [], {"d1": _definition("code")})
    _, _, changes = _plan(bus)

    assert [(c.action, c.target) for c in changes] == [("item.create", "afab_e2e/nb_bronze")]
    assert changes[0].risk.value == "safe"


def test_a_differing_definition_is_planned_as_an_update():
    bus = _bus(
        [_item("d1", "nb_bronze")],
        [_item("t1", "nb_bronze")],
        {"d1": _definition("new"), "t1": _definition("old")},
    )
    _, _, [change] = _plan(bus)

    assert change.action == "item.update" and change.risk.value == "reversible"
    assert change.metadata["target_item_id"] == "t1"


def test_an_identical_definition_is_no_change():
    bus = _bus(
        [_item("d1", "nb_bronze")],
        [_item("t1", "nb_bronze")],
        {"d1": _definition("same", name="dev"), "t1": _definition("same", name="test")},
    )
    _, _, changes = _plan(bus)
    assert changes == []


def test_same_name_different_type_is_a_different_item():
    bus = _bus(
        [_item("d1", "load", "DataPipeline")],
        [_item("t1", "load", "Notebook")],
        {"d1": _definition("a"), "t1": _definition("a")},
    )
    _, _, [change] = _plan(bus)
    assert change.action == "item.create"


def test_extra_items_in_the_target_only_go_with_prune():
    bus = _bus([], [_item("t1", "nb_old")], {"t1": _definition("old")})

    assert _plan(bus)[2] == []

    _, _, [change] = _plan(bus, policy=PRUNING)
    assert change.action == "item.delete" and change.risk.value == "destructive"


def test_types_and_glob_narrow_what_is_compared():
    bus = _bus(
        [_item("d1", "nb_bronze"), _item("d2", "nb_gold"), _item("d3", "pl_load", "DataPipeline")],
        [],
        {k: _definition("code") for k in ("d1", "d2", "d3")},
    )
    _, _, changes = _plan(bus, items="nb_b*", types=["Notebook"])

    assert [c.target for c in changes] == ["afab_e2e/nb_bronze"]
    assert [a["itemId"] for n, a in bus.calls if n == "get_item_definition"] == ["d1"]


def test_a_missing_workspace_refuses_planning():
    bus = _bus([], [], {})
    desired, observed = _run(bus, target="nope")
    assert observed.missing_workspaces == ["nope"]
    with pytest.raises(process.PlanError, match="nope"):
        process.plan(desired, observed, POLICY)


# --- promoting with references ----------------------------------------------------


class TestPromotion:
    def _tenant(self, *, lake_in_target=True, target_notebook=False, lake_name="lh_probe"):
        source = [
            _item(LAKE_SOURCE, "lh_probe", "Lakehouse"),
            _item(NB_SOURCE, "nb_bronze"),
        ]
        target = []
        if lake_in_target:
            target.append(_item(LAKE_TARGET, lake_name, "Lakehouse"))
        if target_notebook:
            target.append(_item("t1", "nb_bronze"))
        definitions = {
            NB_SOURCE: _definition(f"%%configure {SOURCE} {LAKE_SOURCE}"),
            "t1": _definition("stale"),
        }
        return _bus(source, target, definitions)

    def test_a_resolvable_reference_is_planned_with_its_replacement(self):
        _, _, [change] = _plan(self._tenant())
        assert change.action == "item.create"
        assert change.metadata["replacements"] == {SOURCE: TARGET, LAKE_SOURCE: LAKE_TARGET}

    def test_an_unresolvable_reference_blocks_the_item(self):
        _, _, [change] = _plan(self._tenant(lake_in_target=False))
        assert change.action == "item.blocked"
        assert change.before["references"] == ["lh_probe (Lakehouse)"]
        assert "map:" in change.reason

    def test_a_mapping_resolves_it_after_all(self):
        bus = self._tenant(lake_name="lh_test")
        _, _, [change] = _plan(bus, map={"lh_probe": "lh_test"})
        assert change.action == "item.create"
        assert change.metadata["replacements"][LAKE_SOURCE] == LAKE_TARGET

    def test_apply_creates_with_the_ids_rewritten(self):
        bus = self._tenant()
        _, _, changes = _plan(bus)

        outcomes = asyncio.run(process.apply(changes, bus))

        assert [o.ok for o in outcomes] == [True]
        [(_, args)] = [c for c in bus.calls if c[0] == "create_item_with_definition"]
        assert args["workspaceId"] == TARGET and args["displayName"] == "nb_bronze"
        [part] = args["definition"]["parts"]
        assert base64.b64decode(part["payload"]).decode() == f"%%configure {TARGET} {LAKE_TARGET}"

    def test_apply_updates_an_existing_item_in_place(self):
        bus = self._tenant(target_notebook=True)
        _, _, changes = _plan(bus)
        assert [c.action for c in changes] == ["item.update"]

        outcomes = asyncio.run(process.apply(changes, bus))

        assert [o.ok for o in outcomes] == [True]
        [(_, args)] = [c for c in bus.calls if c[0] == "update_item_definition"]
        assert args["itemId"] == "t1"

    def test_a_blocked_item_is_never_written(self):
        bus = self._tenant(lake_in_target=False)
        _, _, changes = _plan(bus)

        outcomes = asyncio.run(process.apply(changes, bus))

        assert not outcomes[0].ok and "unresolved reference" in outcomes[0].detail
        assert not [c for c in bus.calls if c[0] == "create_item_with_definition"]


# --- order ------------------------------------------------------------------------


class TestOrder:
    """A pipeline invoking a notebook has to be created after it."""

    def _tenant(self):
        source = [
            _item(PL_SOURCE, "pl_load", "DataPipeline"),
            _item(NB_SOURCE, "nb_one"),
        ]
        definitions = {
            PL_SOURCE: _definition(f"invoke {NB_SOURCE} in {SOURCE}"),
            NB_SOURCE: _definition("print(1)"),
        }
        return _bus(source, [], definitions)

    def test_the_dependency_is_planned_first(self):
        _, _, changes = _plan(self._tenant())

        assert [c.metadata["name"] for c in changes] == ["nb_one", "pl_load"]
        assert changes[1].metadata["pending"] == {NB_SOURCE: "nb_one\x00Notebook"}

    def test_apply_feeds_the_new_id_into_the_dependant(self):
        bus = self._tenant()
        _, _, changes = _plan(bus)

        outcomes = asyncio.run(process.apply(changes, bus))

        assert [o.ok for o in outcomes] == [True, True]
        _, pipeline = [c for c in bus.calls if c[0] == "create_item_with_definition"][1]
        [part] = pipeline["definition"]["parts"]
        # It invokes the notebook created moments ago, not the one in the source.
        assert base64.b64decode(part["payload"]).decode() == f"invoke new-nb_one in {TARGET}"

    def test_a_blocked_dependency_blocks_its_dependant_too(self):
        # The notebook needs a lakehouse the target lacks; the pipeline needs the
        # notebook. Promoting the pipeline alone would point it at an id that never
        # appears in the target.
        source = [
            _item(PL_SOURCE, "pl_load", "DataPipeline"),
            _item(NB_SOURCE, "nb_one"),
            _item(LAKE_SOURCE, "lh_probe", "Lakehouse"),
        ]
        bus = _bus(
            source,
            [],
            {
                PL_SOURCE: _definition(f"invoke {NB_SOURCE}"),
                NB_SOURCE: _definition(f"attach {LAKE_SOURCE}"),
            },
        )

        _, _, changes = _plan(bus)

        assert {c.action for c in changes} == {"item.blocked"}
        pipeline = next(c for c in changes if c.metadata["name"] == "pl_load")
        assert pipeline.before["references"] == ["nb_one (Notebook)"]

    def test_a_dependency_that_was_never_created_fails_rather_than_writing(self):
        bus = self._tenant()
        _, _, changes = _plan(bus)
        pipeline = [c for c in changes if c.metadata["name"] == "pl_load"]

        [outcome] = asyncio.run(process.apply(pipeline, bus))

        assert not outcome.ok and "nb_one (Notebook)" in outcome.detail


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
    desired, observed, changes = _plan(bus, policy=PRUNING)
    assert {c.action for c in changes} == {"item.create", "item.update", "item.delete"}

    after = process.project(observed, changes)
    assert process.plan(desired, after, PRUNING) == []
    assert len(observed.pairs[0].target_items) == 2


def test_apply_deletes_what_prune_planned():
    bus = _bus([], [_item("t1", "nb_old")], {"t1": _definition("old")})
    _, _, changes = _plan(bus, policy=PRUNING)

    outcomes = asyncio.run(process.apply(changes, bus))

    assert [o.ok for o in outcomes] == [True]
    assert [a["itemId"] for n, a in bus.calls if n == "delete_item"] == ["t1"]
