from afabric.kernel.manifest import ModuleManifest

MANIFEST = ModuleManifest(
    name="deploy",
    version="0.1.0",
    description="Compares item definitions between a source and a target workspace, and "
    "plans what promoting them would take.",
    requires=["fabric.core"],
    provides=[],
    config_key="deploy",
    cli_verbs=[],
)
