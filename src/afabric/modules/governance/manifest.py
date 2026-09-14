from afabric.kernel.manifest import ModuleManifest

MANIFEST = ModuleManifest(
    name="governance",
    version="0.1.0",
    description="Audits workspaces and their items against declared house rules, and "
    "plans what meeting them would take.",
    requires=["fabric.core"],
    provides=[],
    config_key="governance",
    cli_verbs=[],
)
