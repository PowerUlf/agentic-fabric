"""Phase 3: the workspace module's apply, against a bus that records calls."""

import asyncio

from afabric.kernel.desired import PolicyConfig
from afabric.modules.workspace.model import Config, Observed, ObservedRole, ObservedWorkspace
from afabric.modules.workspace.process import apply, plan

ME = "11111111-1111-1111-1111-111111111111"
OTHER = "99999999-9999-9999-9999-999999999999"


class RecordingBus:
    def __init__(self, fail_on: str | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.fail_on = fail_on

    async def call(self, tool: str, **args):
        self.calls.append((tool, args))
        if tool == self.fail_on:
            raise RuntimeError("tenant said no")
        if tool == "create_workspace":
            return {"id": "ws-new", "displayName": args["displayName"]}
        if tool == "create_folder":
            return {"id": "f-new", "displayName": args["displayName"]}
        return None


def run(changes, bus):
    return asyncio.run(apply(changes, bus))


def test_ids_from_a_new_workspace_reach_its_folders_and_roles():
    want = Config.model_validate(
        [
            {
                "name": "scratch",
                "description": "e2e",
                "folders": ["raw"],
                "roles": [{"principal": OTHER, "role": "Viewer"}],
            }
        ]
    )
    changes = plan(want, Observed(), PolicyConfig())
    bus = RecordingBus()

    outcomes = run(changes, bus)

    assert all(o.ok for o in outcomes) and len(outcomes) == 3
    assert bus.calls == [
        ("create_workspace", {"displayName": "scratch", "description": "e2e"}),
        ("create_folder", {"workspaceId": "ws-new", "displayName": "raw"}),
        (
            "add_workspace_role",
            {
                "workspaceId": "ws-new",
                "principal": {"id": OTHER, "type": "User"},
                "role": "Viewer",
            },
        ),
    ]
    assert outcomes[0].output == {"workspace_id": "ws-new"}
    assert outcomes[1].output == {"folder_id": "f-new"}


def test_existing_workspace_uses_its_observed_id():
    observed = Observed(
        workspaces=[
            ObservedWorkspace(
                id="ws-1",
                name="team",
                details_loaded=True,
                roles=[
                    ObservedRole(assignment_id="ra-1", principal=OTHER, type="User", role="Member")
                ],
            )
        ]
    )
    want = Config.model_validate(
        [{"name": "team", "description": "new", "roles": [{"principal": OTHER, "role": "Viewer"}]}]
    )
    bus = RecordingBus()

    run(plan(want, observed, PolicyConfig()), bus)

    assert bus.calls == [
        ("update_workspace", {"workspaceId": "ws-1", "description": "new"}),
        (
            "update_workspace_role",
            {"workspaceId": "ws-1", "roleAssignmentId": "ra-1", "role": "Viewer"},
        ),
    ]


def test_stops_at_the_first_failure():
    want = Config.model_validate([{"name": "scratch", "folders": ["a", "b"]}])
    bus = RecordingBus(fail_on="create_folder")

    outcomes = run(plan(want, Observed(), PolicyConfig()), bus)

    assert [o.ok for o in outcomes] == [True, False]
    assert "tenant said no" in outcomes[1].detail
    assert [tool for tool, _ in bus.calls] == ["create_workspace", "create_folder"]


def test_unknown_action_fails_instead_of_passing_silently():
    from afabric.model.change import Change, Risk

    change = Change(module="workspace", action="workspace.teleport", target="x", risk=Risk.SAFE)
    [outcome] = run([change], RecordingBus())
    assert not outcome.ok and "workspace.teleport" in outcome.detail
