"""Phase 2: the workspace module's pure plan, against a snapshot recorded from a real tenant.

tests/fixtures/workspace_observed.json was captured with `observe()` over both
transports (which agreed) and then anonymized. It holds a personal workspace and one
team workspace whose only role assignment is the signed-in user as Admin — the exact
situation in which pruning roles would lock the user out.
"""

import json
from pathlib import Path

import pytest

from afabric.kernel.desired import PolicyConfig
from afabric.model.change import Risk
from afabric.modules.workspace.model import Config, Observed, ObservedFolder
from afabric.modules.workspace.process import PlanError, plan, project

FIXTURE = Path(__file__).parent / "fixtures" / "workspace_observed.json"
ME = "11111111-1111-1111-1111-111111111111"
OTHER = "99999999-9999-9999-9999-999999999999"

KEEP = PolicyConfig()
PRUNE = PolicyConfig(prune=True)


@pytest.fixture
def observed() -> Observed:
    return Observed.model_validate(json.loads(FIXTURE.read_text()))


def desired(*workspaces: dict) -> Config:
    return Config.model_validate(list(workspaces))


def actions(changes):
    return [c.action for c in changes]


def assert_converges(want: Config, observed: Observed, policy: PolicyConfig):
    """Planning against the projected result of a plan must yield nothing."""
    changes = plan(want, observed, policy)
    after = project(observed, changes)
    assert plan(want, after, policy) == [], "plan is not idempotent"
    return changes


class TestNoDrift:
    def test_declaring_a_name_only_changes_nothing(self, observed):
        assert plan(desired({"name": "team_dev"}), observed, KEEP) == []

    def test_matching_state_changes_nothing(self, observed):
        want = desired(
            {
                "name": "team_dev",
                "description": "",
                "capacity": "cap-f4",
                "roles": [{"principal": ME, "role": "Admin"}],
            }
        )
        assert plan(want, observed, KEEP) == []
        assert plan(want, observed, PRUNE) == []


class TestCreation:
    def test_new_workspace_with_folders_and_roles(self, observed):
        want = desired(
            {
                "name": "sales",
                "capacity": "cap-f4",
                "folders": ["Bronze", "Gold"],
                "roles": [{"principal": OTHER, "role": "Viewer"}],
            }
        )
        changes = assert_converges(want, observed, KEEP)

        assert actions(changes) == [
            "workspace.create",
            "folder.create",
            "folder.create",
            "role.grant",
        ]
        create = changes[0]
        assert create.risk is Risk.SAFE
        assert create.after["capacity_id"] == observed.capacities["cap-f4"]

    def test_own_admin_role_is_not_granted_on_a_workspace_we_create(self, observed):
        # Fabric makes the creator an Admin; planning that grant would fail on apply.
        observed.identity = ME
        want = desired(
            {
                "name": "sales",
                "roles": [
                    {"principal": ME, "role": "Admin"},
                    {"principal": OTHER, "role": "Viewer"},
                ],
            }
        )

        changes = plan(want, observed, KEEP)

        assert actions(changes) == ["workspace.create", "role.grant"]
        assert changes[1].target.endswith(OTHER)

    def test_without_a_known_identity_every_declared_role_is_granted(self, observed):
        observed.identity = None
        want = desired({"name": "sales", "roles": [{"principal": ME, "role": "Admin"}]})

        assert actions(plan(want, observed, KEEP)) == ["workspace.create", "role.grant"]

    def test_an_existing_workspace_still_grants_our_own_role(self, observed):
        # Only creation is special: on a workspace that already exists, a missing
        # assignment for the caller is real drift.
        observed.identity = OTHER
        want = desired({"name": "team_dev", "roles": [{"principal": OTHER, "role": "Viewer"}]})

        assert actions(plan(want, observed, KEEP)) == ["role.grant"]

    def test_role_grant_is_never_safe(self, observed):
        want = desired({"name": "team_dev", "roles": [{"principal": OTHER, "role": "Viewer"}]})
        (grant,) = plan(want, observed, KEEP)
        assert grant.action == "role.grant"
        assert grant.risk is Risk.REVERSIBLE


class TestDrift:
    def test_description(self, observed):
        want = desired({"name": "team_dev", "description": "now documented"})
        (change,) = assert_converges(want, observed, KEEP)
        assert change.action == "workspace.update"
        assert change.before == {"description": ""}
        assert change.risk is Risk.REVERSIBLE

    def test_capacity(self, observed):
        want = desired({"name": "team_dev", "capacity": "Premium Per User - Reserved"})
        (change,) = assert_converges(want, observed, KEEP)
        assert change.action == "workspace.assign_capacity"

    def test_role_change(self, observed):
        want = desired({"name": "team_dev", "roles": [{"principal": ME, "role": "Member"}]})
        (change,) = assert_converges(want, observed, KEEP)
        assert change.action == "role.update"
        assert (change.before, change.after) == ({"role": "Admin"}, {"role": "Member"})

    def test_missing_folder(self, observed):
        want = desired({"name": "team_dev", "folders": ["Bronze"]})
        (change,) = assert_converges(want, observed, KEEP)
        assert change.action == "folder.create"
        assert change.target == "team_dev/Bronze"


class TestPrune:
    """Pruning is where a reconciler does real damage, so it is off unless asked for."""

    def test_off_by_default_nothing_undeclared_is_touched(self, observed):
        # Declares nothing about the existing role or about the personal workspace.
        changes = plan(desired({"name": "sales"}), observed, KEEP)
        assert "workspace.delete" not in actions(changes)
        assert "role.revoke" not in actions(changes)

    def test_on_proposes_removals_as_destructive(self, observed):
        changes = assert_converges(desired({"name": "team_dev"}), observed, PRUNE)
        (revoke,) = changes
        assert revoke.action == "role.revoke"
        assert revoke.risk is Risk.DESTRUCTIVE
        # This is the signed-in user's own Admin role. The reason has to say so.
        assert revoke.metadata["principal"] == ME
        assert "your own access" in revoke.reason

    def test_undeclared_workspace_deleted_but_never_the_personal_one(self, observed):
        changes = assert_converges(desired(), observed, PRUNE)
        deleted = [c.target for c in changes if c.action == "workspace.delete"]
        assert deleted == ["team_dev"]

    def test_undeclared_folder(self, observed):
        observed.by_name()["team_dev"].folders.append(ObservedFolder(id="f-1", name="Scratch"))
        want = desired({"name": "team_dev", "roles": [{"principal": ME, "role": "Admin"}]})

        assert plan(want, observed, KEEP) == []
        (delete,) = assert_converges(want, observed, PRUNE)
        assert delete.action == "folder.delete"
        assert delete.risk is Risk.DESTRUCTIVE


class TestRefusals:
    def test_unknown_capacity(self, observed):
        with pytest.raises(PlanError, match="capacity 'nope' not found.*cap-f4"):
            plan(desired({"name": "team_dev", "capacity": "nope"}), observed, KEEP)

    def test_personal_workspace_cannot_be_managed(self, observed):
        with pytest.raises(PlanError, match="personal workspace"):
            plan(desired({"name": "My workspace", "description": "x"}), observed, KEEP)

    def test_details_not_observed_is_not_read_as_empty(self, observed):
        observed.by_name()["team_dev"].details_loaded = False
        with pytest.raises(PlanError, match="not observed"):
            plan(desired({"name": "team_dev"}), observed, KEEP)


def test_plan_is_pure(observed):
    before = observed.model_dump()
    plan(desired({"name": "sales", "folders": ["x"]}, {"name": "team_dev"}), observed, PRUNE)
    assert observed.model_dump() == before
