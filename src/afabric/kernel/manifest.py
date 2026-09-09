"""What a module declares about itself.

This lives in the kernel rather than in each module so that discovery can read a
manifest without importing the module's implementation.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ModuleManifest(BaseModel):
    """A module's self-description.

    `requires` and `provides` name capabilities, not modules. That indirection is what
    keeps modules from importing each other: the ToolBus resolves a capability to
    whichever module currently offers it.
    """

    name: str
    version: str = "0.1.0"
    description: str = ""

    requires: list[str] = Field(default_factory=list)
    """Capabilities this module needs, e.g. `fabric.core`."""

    provides: list[str] = Field(default_factory=list)
    """Capabilities this module offers to others, e.g. `workspace.ensure`."""

    config_key: str | None = None
    """Its top-level key in fabric.yaml. None means the module needs no configuration."""

    cli_verbs: list[str] = Field(default_factory=list)
    """Extra `afab` subcommands this module contributes."""
