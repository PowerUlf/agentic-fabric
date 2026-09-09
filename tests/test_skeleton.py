"""Phase 0 smoke tests: the skeleton holds together and the contracts import."""

from typer.testing import CliRunner

from afabric.cli import app
from afabric.kernel.manifest import ModuleManifest
from afabric.model import Change, Risk
from afabric.modules.workspace import MANIFEST

runner = CliRunner()


def test_cli_runs():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "agentic-fabric" in result.stdout


def test_workspace_manifest_is_well_formed():
    assert isinstance(MANIFEST, ModuleManifest)
    assert MANIFEST.name == "workspace"
    assert MANIFEST.config_key == "workspaces"
    assert "workspace.ensure" in MANIFEST.provides


def test_change_carries_risk():
    change = Change(
        module="workspace",
        action="role.revoke",
        target="Sales Analytics",
        risk=Risk.DESTRUCTIVE,
        before={"role": "Admin"},
    )
    assert change.risk is Risk.DESTRUCTIVE
    assert change.summary() == "role.revoke Sales Analytics"
