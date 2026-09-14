"""The `deploy:` section of fabric.yaml, and the two workspaces it compares."""

from __future__ import annotations

import base64
import hashlib
import re
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

    map: dict[str, str] = Field(default_factory=dict)
    """Source item name -> target item name, for things this promotion does not carry.

    A notebook attached to a lakehouse holds that lakehouse's id. If the lakehouse is not
    promoted, the reference can only be resolved by being told which item in the target
    stands for it.
    """

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
    """A stable fingerprint of an item's definition, ignoring its platform metadata."""
    if definition is None:
        return None
    return digest_parts(definition.get("parts", []))


def digest_parts(parts: list[dict[str, Any]]) -> str | None:
    """Fingerprint a list of definition parts.

    Parts come back in no guaranteed order, so they are sorted by path before hashing.
    """
    parts = sorted(
        (p.get("path", ""), p.get("payload", ""))
        for p in parts
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


GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)

# The all-zero guid stands in .platform for "no logical id yet"; it is not a reference.
EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def references(definition: dict[str, Any] | None) -> set[str]:
    """Every id a definition mentions, `.platform` aside.

    A notebook holds the workspace and lakehouse it attaches to; a pipeline holds the
    items it invokes. Promoting either without rewriting these means the copy reaches
    back into the workspace it came from.
    """
    found: set[str] = set()
    for part in (definition or {}).get("parts", []):
        if part.get("path") == PLATFORM_PART:
            continue
        text = base64.b64decode(part.get("payload", "")).decode("utf-8", "replace")
        found.update(guid.lower() for guid in GUID.findall(text))
    return found - {EMPTY_GUID}


def rewrite(definition: dict[str, Any], replacements: dict[str, str]) -> list[dict[str, Any]]:
    """The definition's parts with every known id swapped, `.platform` dropped.

    `.platform` carries the display name and the logical id of the source item; letting
    it through would stamp the copy with the original's identity.
    """
    parts: list[dict[str, Any]] = []
    for part in definition.get("parts", []):
        if part.get("path") == PLATFORM_PART:
            continue
        text = base64.b64decode(part["payload"]).decode("utf-8")
        for source, target in replacements.items():
            # Ids appear in both cases in practice; normalise on the way in.
            text = re.sub(re.escape(source), target, text, flags=re.I)
        parts.append(
            {
                "path": part["path"],
                "payload": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                "payloadType": "InlineBase64",
            }
        )
    return parts


class ObservedItem(BaseModel):
    id: str
    name: str
    type: str
    digest: str | None = None
    """None when the item type carries no definition this API returns."""

    references: set[str] = Field(default_factory=set)
    """Ids this item's definition mentions. Empty when no definition was fetched."""

    parts: list[dict[str, Any]] = Field(default_factory=list)
    """The definition itself, needed to fingerprint what a promotion would produce."""


class ObservedPair(BaseModel):
    source: str
    source_id: str
    target: str
    target_id: str
    source_items: list[ObservedItem] = []
    target_items: list[ObservedItem] = []

    source_all: dict[str, str] = Field(default_factory=dict)
    """Every item in the source, id -> `name\\x00type`. Needed to name a reference."""

    target_all: dict[str, str] = Field(default_factory=dict)
    """Every item in the target, `name\\x00type` -> id. Needed to resolve one."""


class Observed(BaseModel):
    pairs: list[ObservedPair] = []
    missing_workspaces: list[str] = []
