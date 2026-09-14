"""The `deploy:` section of fabric.yaml, and the two workspaces it compares."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

# Fabric derives these from another item; they are created with their parent, carry no
# definition worth promoting, and cannot be deployed on their own.
DERIVED_TYPES: frozenset[str] = frozenset({"SQLEndpoint", "SemanticModel"})

# Item metadata: display name, logical id, the workspace it came from. It differs between
# workspaces by definition, so comparing it would report every item as changed forever.
PLATFORM_PART = ".platform"


class PromotionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    """Workspace display name to promote from. Never written to."""

    target: str = Field(min_length=1)
    """Workspace display name to promote into."""

    items: str = "*"
    """Glob over item display names."""

    types: list[str] = Field(default_factory=lambda: ["Notebook", "DataPipeline"])
    """Item types to promote. Derived types are refused: they follow their parent."""

    @model_validator(mode="after")
    def _distinct_and_deployable(self) -> PromotionSpec:
        if self.source == self.target:
            raise ValueError(f"source and target are the same workspace: {self.source!r}")
        derived = sorted(set(self.types) & DERIVED_TYPES)
        if derived:
            raise ValueError(
                f"{', '.join(derived)} cannot be deployed on its own; it follows its parent item"
            )
        if not self.types:
            raise ValueError("types is empty — nothing would be compared")
        return self


class Config(RootModel[list[PromotionSpec]]):
    """Validated `deploy:` section."""

    @model_validator(mode="after")
    def _unique_pairs(self) -> Config:
        pairs = [(p.source, p.target, p.items) for p in self.root]
        duplicates = sorted({f"{s} -> {t}" for s, t, i in pairs if pairs.count((s, t, i)) > 1})
        if duplicates:
            raise ValueError(f"the same promotion declared twice: {', '.join(duplicates)}")
        return self

    def __iter__(self):
        return iter(self.root)


# --- observed ---------------------------------------------------------------------


def digest(definition: dict[str, Any] | None) -> str | None:
    """A stable fingerprint of an item's definition, ignoring its platform metadata.

    Parts come back in no guaranteed order, so they are sorted by path before hashing.
    """
    if definition is None:
        return None
    parts = sorted(
        (p.get("path", ""), p.get("payload", ""))
        for p in definition.get("parts", [])
        if p.get("path") != PLATFORM_PART
    )
    if not parts:
        return None
    sha = hashlib.sha256()
    for path, payload in parts:
        sha.update(path.encode())
        sha.update(b"\0")
        sha.update(payload.encode())
        sha.update(b"\0")
    return sha.hexdigest()


class ObservedItem(BaseModel):
    id: str
    name: str
    type: str
    digest: str | None = None
    """None when the item type carries no definition this API returns."""


class ObservedPair(BaseModel):
    source: str
    source_id: str
    target: str
    target_id: str
    source_items: list[ObservedItem] = []
    target_items: list[ObservedItem] = []


class Observed(BaseModel):
    pairs: list[ObservedPair] = []
    missing_workspaces: list[str] = []
