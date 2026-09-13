from afabric.kernel.manifest import ModuleManifest

MANIFEST = ModuleManifest(
    name="job-health",
    version="0.1.0",
    description="Watches scheduled and manual job runs of items, and plans what a "
    "declared health expectation would take.",
    requires=["fabric.core"],
    provides=[],
    config_key="jobs",
    cli_verbs=[],
)
