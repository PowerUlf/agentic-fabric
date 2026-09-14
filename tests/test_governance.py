"""Phase 5: the third module — house rules audited, nothing written.

This one is the measure: it adds no tools, no kernel change, nothing but a directory.
"""

import asyncio

import pytest
from pydantic import ValidationError

from afabric.kernel.desired import PolicyConfig
from afabric.modules.governance import process
from afabric.modules.governance.model import Config

POLICY = PolicyConfig()

WORKSPACES = [
    {"id": "w1", "displayName": "faf_dev", "description": "", "capacityId": "cap1"},
    {"id": "w2", "displayName": "afab_e2e", "description": "End-to-end", "capacityId": None},
    {"id": "w3", "displayName": "My workspace", "type": "Personal"},
]

ITEMS = {
    "w1": [
        {"id": "i1", "displayName": "nb_bronze", "type": "Notebook", "description": "loads"},
        {"id": "i2", "displayName": "NB Gold", "type": "Notebook", "description": ""},
        {"id": "i3", "displayName": "lh_probe", "type": "SQLEndpoint", "description": ""},
    ],
    "w2": [],
}

ROLES = {
    "w1": [
        {"id": "r1", "role": "Admin", "principal": {"id": "p1"}},
        {"id": "r2", "role": "Member", "principal": {"id": "p2"}},
    ],
    "w2": [{"id": "r3", "role": "Admin", "principal": {"id": "p1"}}],
}


class FakeBus:
    def __init__(self):
        self.calls = []

    async def call(self, name, **args):
        self.calls.append((name, args))
        if name == "list_workspaces":
            return WORKSPACES
        if name == "list_items":
            return ITEMS[args["workspaceId"]]
        if name == "list_workspace_roles":
            return ROLES[args["workspaceId"]]
        raise AssertionError(f"unexpected tool {name}")


def _run(**rules):
    bus = FakeBus()
    desired = Config.model_validate(rules)
    observed = asyncio.run(process.observe(bus, desired))
    return desired, observed, bus


def _plan(**rules):
    desired, observed, _ = _run(**rules)
    return process.plan(desired, observed, POLICY)


# --- the declaration --------------------------------------------------------------


def test_an_invalid_regex_is_refused():
    with pytest.raises(ValidationError, match="not a valid regex"):
        Config.model_validate({"item_naming": "["})


def test_an_unknown_rule_is_refused():
    with pytest.raises(ValidationError):
        Config.model_validate({"require_everything": True})


# --- observe ----------------------------------------------------------------------


def test_reads_only_what_the_rules_need():
    _, _, bus = _run(workspace_description=True)
    assert {name for name, _ in bus.calls} == {"list_workspaces"}

    _, _, bus = _run(item_naming="^[a-z]", min_admins=1)
    assert {name for name, _ in bus.calls} == {
        "list_workspaces",
        "list_items",
        "list_workspace_roles",
    }


def test_personal_workspaces_and_derived_items_are_out_of_scope():
    _, observed, _ = _run(item_description=["Notebook"])
    assert [w.name for w in observed.workspaces] == ["faf_dev", "afab_e2e"]
    # The SQL endpoint belongs to the lakehouse; it has no description of its own.
    assert [i.name for i in observed.workspaces[0].items] == ["nb_bronze", "NB Gold"]


def test_scope_narrows_to_matching_workspaces():
    _, observed, _ = _run(scope="faf_*", workspace_description=True)
    assert [w.name for w in observed.workspaces] == ["faf_dev"]


def test_nothing_declared_reads_nothing():
    desired, observed, bus = _run()
    assert bus.calls == [] and observed.workspaces == []
    assert process.plan(desired, observed, POLICY) == []


# --- plan -------------------------------------------------------------------------


def test_missing_workspace_description_is_a_finding():
    changes = _plan(workspace_description=True)
    assert [(c.action, c.target) for c in changes] == [("governance.describe", "faf_dev")]
    assert changes[0].risk.value == "safe"


def test_missing_capacity_is_a_finding():
    changes = _plan(capacity_required=True)
    assert [c.target for c in changes] == ["afab_e2e"]


def test_too_few_admins_is_a_finding():
    # faf_dev has one Admin and one Member, afab_e2e one Admin: both fall short of two.
    changes = _plan(min_admins=2)
    assert [(c.target, c.before["admins"]) for c in changes] == [("faf_dev", 1), ("afab_e2e", 1)]
    assert _plan(min_admins=1) == []


def test_item_rules_report_per_item():
    changes = _plan(item_description=["Notebook"], item_naming="^[a-z][a-z0-9_]*$")
    assert [(c.action, c.target) for c in changes] == [
        ("governance.describe", "faf_dev/NB Gold"),
        ("governance.rename", "faf_dev/NB Gold"),
    ]
    rename = changes[1]
    assert rename.risk.value == "reversible"
    assert "^[a-z][a-z0-9_]*$" in rename.reason


def test_a_compliant_tenant_plans_nothing():
    assert _plan(scope="afab_*", item_description=["Notebook"], min_admins=1) == []


def test_unobserved_state_refuses_rather_than_passing():
    desired, observed, _ = _run(workspace_description=True)
    strict = Config.model_validate({"item_naming": "^x"})
    with pytest.raises(process.PlanError, match="items were not observed"):
        process.plan(strict, observed, POLICY)


# --- project ----------------------------------------------------------------------


def test_planning_against_the_projection_is_empty():
    desired, observed, _ = _run(
        workspace_description=True,
        capacity_required=True,
        min_admins=2,
        item_description=["Notebook"],
        item_naming="^[a-z][a-z0-9_]*$",
    )
    changes = process.plan(desired, observed, POLICY)
    assert [(c.action, c.target) for c in changes] == [
        ("governance.describe", "faf_dev"),
        ("governance.grant_admin", "faf_dev"),
        ("governance.describe", "faf_dev/NB Gold"),
        ("governance.rename", "faf_dev/NB Gold"),
        ("governance.assign_capacity", "afab_e2e"),
        ("governance.grant_admin", "afab_e2e"),
    ]

    after = process.project(observed, changes)
    assert process.plan(desired, after, POLICY) == []
    assert observed.workspaces[0].description == ""


# --- apply ------------------------------------------------------------------------


def test_apply_points_at_the_module_that_can_fix_it():
    desired, observed, bus = _run(workspace_description=True)
    changes = process.plan(desired, observed, POLICY)

    outcomes = asyncio.run(process.apply(changes, bus))

    assert len(outcomes) == 1 and not outcomes[0].ok
    assert "workspaces:" in outcomes[0].detail
