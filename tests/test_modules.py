"""Phase 2: module discovery.

The modularity test lives here. A module written as a plain directory must be found,
registered and listed without a single change to the kernel — if that ever needs a
kernel edit, the architecture has failed its main promise.
"""

from pathlib import Path
from textwrap import dedent

from typer.testing import CliRunner

from afabric.cli import app
from afabric.kernel.modules import discover


def _write_module(root: Path, name: str, manifest_body: str, **components: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "manifest.py").write_text(dedent(manifest_body))
    for component, body in components.items():
        (directory / f"{component}.py").write_text(dedent(body))
    return directory


DUMMY_MANIFEST = """
    from afabric.kernel.manifest import ModuleManifest

    MANIFEST = ModuleManifest(
        name="dummy",
        version="9.9.9",
        requires=["fabric.core", "workspace.ensure"],
        provides=["dummy.ping"],
        config_key="dummies",
    )
"""


class TestModularity:
    def test_a_directory_is_enough(self, tmp_path):
        _write_module(tmp_path, "dummy", DUMMY_MANIFEST, process="def plan(*a): return []\n")

        registry = discover([tmp_path])

        assert registry.ok, registry.problems
        assert "dummy" in registry.modules
        dummy = registry.modules["dummy"]
        assert dummy.origin == str(tmp_path / "dummy")
        assert dummy.manifest.version == "9.9.9"
        assert dummy.component("process").plan() == []
        # It depends on the workspace module by capability, not by import.
        assert "workspace.ensure" in registry.provided_capabilities()

    def test_cli_lists_it(self, tmp_path, monkeypatch):
        _write_module(tmp_path, "dummy", DUMMY_MANIFEST)
        monkeypatch.setenv("AFABRIC_MODULE_PATH", str(tmp_path))

        result = CliRunner().invoke(app, ["modules"])

        assert result.exit_code == 0, result.output
        assert "dummy" in result.output
        assert "workspace" in result.output


TOOLS = """
    from afabric.kernel.tools import RestOp, ToolSpec

    SPECS = [
        ToolSpec(
            name="{name}",
            description="a module's own endpoint",
            rest=RestOp("GET", "/workspaces/{{workspaceId}}/things"),
            paged=True,
        )
    ]
"""


class TestModuleTools:
    def test_a_module_brings_its_own_tools(self, tmp_path):
        _write_module(tmp_path, "dummy", DUMMY_MANIFEST, tools=TOOLS.format(name="list_things"))

        registry = discover([tmp_path])

        assert registry.ok, registry.problems
        assert registry.tool_specs()["list_things"].rest.path == "/workspaces/{workspaceId}/things"

    def test_a_module_may_not_shadow_a_catalog_tool(self, tmp_path):
        _write_module(tmp_path, "dummy", DUMMY_MANIFEST, tools=TOOLS.format(name="list_workspaces"))

        registry = discover([tmp_path])

        assert not registry.ok
        assert "already in the kernel catalog" in registry.problems[0].message

    def test_two_modules_may_not_declare_the_same_tool(self, tmp_path):
        _write_module(tmp_path, "dummy", DUMMY_MANIFEST, tools=TOOLS.format(name="list_things"))
        _write_module(
            tmp_path,
            "other",
            DUMMY_MANIFEST.replace('name="dummy"', 'name="other"')
            .replace('config_key="dummies"', 'config_key="others"')
            .replace('provides=["dummy.ping"]', 'provides=["other.ping"]'),
            tools=TOOLS.format(name="list_things"),
        )

        registry = discover([tmp_path])

        assert not registry.ok
        assert "already declared by" in registry.problems[0].message

    def test_the_job_health_module_owns_its_endpoints(self):
        from afabric.kernel.tools import CATALOG

        specs = discover().tool_specs()

        assert set(specs) == {"list_item_job_instances", "list_item_schedules"}
        # They are the module's, not the kernel's.
        assert not set(specs) & set(CATALOG)


class TestBuiltins:
    def test_workspace_found_once_despite_two_sources(self):
        # Built-in scan and the entry point both see it; that is one module, not a clash.
        registry = discover()
        assert registry.ok, registry.problems
        assert sorted(registry.modules) == ["job-health", "workspace"]
        assert registry.modules["workspace"].origin == "builtin"
        assert registry.modules["job-health"].origin == "builtin"

    def test_missing_component_is_none(self):
        workspace = discover().modules["workspace"]
        assert workspace.component("tools") is None
        assert workspace.component("process") is not None


class TestProblemsAreReported:
    def _messages(self, registry):
        return [p.message for p in registry.problems]

    def test_manifest_that_does_not_import(self, tmp_path):
        _write_module(tmp_path, "broken", "raise RuntimeError('boom')\n")
        registry = discover([tmp_path])
        assert "broken" not in registry.modules
        assert any("failed to import" in m and "boom" in m for m in self._messages(registry))

    def test_manifest_of_the_wrong_type(self, tmp_path):
        _write_module(tmp_path, "wrong", "MANIFEST = {'name': 'wrong'}\n")
        registry = discover([tmp_path])
        assert any("ModuleManifest" in m for m in self._messages(registry))

    def test_name_taken_by_another_package(self, tmp_path):
        _write_module(
            tmp_path,
            "impostor",
            """
            from afabric.kernel.manifest import ModuleManifest
            MANIFEST = ModuleManifest(name="workspace")
            """,
        )
        registry = discover([tmp_path])
        assert registry.modules["workspace"].origin == "builtin"
        assert any("already defined" in m for m in self._messages(registry))

    def test_unmet_requirement(self, tmp_path):
        _write_module(
            tmp_path,
            "needy",
            """
            from afabric.kernel.manifest import ModuleManifest
            MANIFEST = ModuleManifest(name="needy", requires=["nobody.offers.this"])
            """,
        )
        registry = discover([tmp_path])
        assert any("nobody.offers.this" in m for m in self._messages(registry))

    def test_reserved_and_duplicate_config_keys(self, tmp_path):
        _write_module(
            tmp_path,
            "greedy",
            """
            from afabric.kernel.manifest import ModuleManifest
            MANIFEST = ModuleManifest(name="greedy", config_key="policy")
            """,
        )
        _write_module(
            tmp_path,
            "copycat",
            """
            from afabric.kernel.manifest import ModuleManifest
            MANIFEST = ModuleManifest(name="copycat", config_key="workspaces")
            """,
        )
        messages = self._messages(discover([tmp_path]))
        assert any("reserved for the kernel" in m for m in messages)
        assert any("already owned by 'workspace'" in m for m in messages)

    def test_capability_provided_twice(self, tmp_path):
        _write_module(
            tmp_path,
            "rival",
            """
            from afabric.kernel.manifest import ModuleManifest
            MANIFEST = ModuleManifest(name="rival", provides=["workspace.ensure"])
            """,
        )
        messages = self._messages(discover([tmp_path]))
        assert any("already provided by 'workspace'" in m for m in messages)

    def test_module_path_entry_that_is_not_a_directory(self, tmp_path):
        registry = discover([tmp_path / "missing"])
        assert any("not a directory" in m for m in self._messages(registry))

    def test_cli_exits_nonzero_on_problems(self, tmp_path, monkeypatch):
        _write_module(tmp_path, "broken", "raise RuntimeError('boom')\n")
        monkeypatch.setenv("AFABRIC_MODULE_PATH", str(tmp_path))
        result = CliRunner().invoke(app, ["modules"])
        assert result.exit_code == 1
        assert "boom" in result.output
