"""The `governance:` section of fabric.yaml, and the observed state it is audited against.

Unlike `workspaces:`, this section is one object, not a list: house rules apply across a
scope rather than to one named thing.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Items Fabric derives from another item. They carry no description of their own and
# cannot be renamed independently, so auditing them reports noise nobody can act on.
DERIVED_TYPES: frozenset[str] = frozenset({"SQLEndpoint", "SemanticModel"})


class Config(BaseModel):
    """Validated `governance:` section."""

    model_config = ConfigDict(extra="forbid")

    scope: str = "*"
    """Glob over workspace display names. Personal workspaces are never in scope."""

    workspace_description: bool = False
    """Every workspace in scope must carry a description."""

    item_description: list[str] = Field(default_factory=list)
    """Item types that must carry a description, e.g. `[Notebook, DataPipeline]`."""

    item_naming: str | None = None
    """Regex every item name in scope must match."""

    min_admins: int | None = Field(default=None, ge=1)
    """Fewest Admin role assignments a workspace must have."""

    capacity_required: bool = False
    """Every workspace in scope must sit on a capacity."""

    @field_validator("item_naming")
    @classmethod
    def _valid_regex(cls, pattern: str | None) -> str | None:
        if pattern is None:
            return None
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"item_naming is not a valid regex: {exc}") from None
        return pattern

    @property
    def audits_items(self) -> bool:
        return bool(self.item_description) or self.item_naming is not None

    @property
    def audits_anything(self) -> bool:
        return (
            self.workspace_description
            or self.capacity_required
            or self.min_admins is not None
            or self.audits_items
        )


# --- observed ---------------------------------------------------------------------


class ObservedItem(BaseModel):
    id: str
    name: str
    type: str
    description: str = ""


class ObservedWorkspace(BaseModel):
    id: str
    name: str
    description: str = ""
    capacity_id: str | None = None
    admins: int = 0
    items: list[ObservedItem] = []
    items_loaded: bool = False
    """False when nothing declared needed them — never read as "it has none"."""

    roles_loaded: bool = False


class Observed(BaseModel):
    workspaces: list[ObservedWorkspace] = []
