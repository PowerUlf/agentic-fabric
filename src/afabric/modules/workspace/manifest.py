from afabric.kernel.manifest import ModuleManifest

MANIFEST = ModuleManifest(
    name="workspace",
    version="0.1.0",
    description="Reconciles Fabric workspaces, folders and role assignments "
    "against a declared desired state.",
    requires=["fabric.core"],
    provides=["workspace.ensure"],
    config_key="workspaces",
    cli_verbs=[],
)
