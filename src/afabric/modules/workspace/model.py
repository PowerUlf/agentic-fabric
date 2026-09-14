"""The `workspaces:` section of fabric.yaml, and the observed state it is compared to."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from afabric.kernel.desired import DesiredStateError, Fragment

PrincipalType = Literal["User", "Group", "ServicePrincipal"]
WorkspaceRole = Literal["Admin", "Member", "Contributor", "Viewer"]


# --- desired ----------------------------------------------------------------------


class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: UUID
    """Entra object id. Email resolution needs the Graph MCP server; not wired yet."""

    type: PrincipalType = "User"
    role: WorkspaceRole


class WorkspaceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    """Identity. Renaming in YAML means delete-and-create, so it is never inferred."""

    description: str | None = None
    """None means "don't manage"; an empty string means "must be empty"."""

    capacity: str | None = None
    """Capacity display name, resolved to an id against the tenant."""

    folders: list[str] = []
    roles: list[RoleSpec] = []

    @field_validator("folders")
    @classmethod
    def _unique_folders(cls, folders: list[str]) -> list[str]:
        seen: set[str] = set()
        for folder in folders:
            if folder in seen:
                raise ValueError(f"folder {folder!r} is listed twice")
            seen.add(folder)
        return folders

    @field_validator("roles")
    @classmethod
    def _one_role_per_principal(cls, roles: list[RoleSpec]) -> list[RoleSpec]:
        seen: set[UUID] = set()
        for role in roles:
            if role.principal in seen:
                raise ValueError(f"principal {role.principal} has more than one role")
            seen.add(role.principal)
        return roles


class Config(RootModel[list[WorkspaceSpec]]):
    """Validated `workspaces:` section."""

    @model_validator(mode="after")
    def _unique_names(self) -> Config:
        names = [w.name for w in self.root]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise ValueError(f"workspace names must be unique: {', '.join(duplicates)}")
        return self

    def __iter__(self):
        return iter(self.root)

    def by_name(self) -> dict[str, WorkspaceSpec]:
        return {w.name: w for w in self.root}


def merge(fragments: list[Fragment]) -> list[dict[str, Any]]:
    """Merge workspace lists across the cascade, identified by `name`.

    A later file that mentions an existing workspace extends it rather than declaring a
    second one: scalars are overridden, folders are unioned, roles are keyed by
    principal with the later role winning. Within a single file a name may appear only
    once — that is a typo, not a layering decision.
    """
    problems: list[str] = []
    order: list[str] = []
    merged: dict[str, dict[str, Any]] = {}

    for fragment in fragments:
        if fragment.data is None:
            continue
        if not isinstance(fragment.data, list):
            problems.append(f"{fragment.source}: workspaces must be a list")
            continue

        seen_here: set[str] = set()
        for entry in fragment.data:
            if not isinstance(entry, dict) or "name" not in entry:
                problems.append(f"{fragment.source}: every workspace needs a name")
                continue
            name = entry["name"]
            if name in seen_here:
                problems.append(f"{fragment.source}: workspace {name!r} is declared twice")
                continue
            seen_here.add(name)

            if name not in merged:
                order.append(name)
                merged[name] = dict(entry)
            else:
                merged[name] = _extend(merged[name], entry)

    if problems:
        raise DesiredStateError(problems)
    return [merged[name] for name in order]


def _extend(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in overlay.items():
        if key == "folders":
            existing = list(result.get("folders") or [])
            result["folders"] = existing + [f for f in value or [] if f not in existing]
        elif key == "roles":
            by_principal = {str(r.get("principal")): r for r in result.get("roles") or []}
            for role in value or []:
                by_principal[str(role.get("principal"))] = role
            result["roles"] = list(by_principal.values())
        else:
            result[key] = value
    return result


# --- observed ---------------------------------------------------------------------


class ObservedRole(BaseModel):
    assignment_id: str
    principal: UUID
    type: str
    role: str


class ObservedFolder(BaseModel):
    id: str
    name: str


class ObservedWorkspace(BaseModel):
    id: str
    name: str
    description: str = ""
    capacity_id: str | None = None
    type: str = "Workspace"

    details_loaded: bool = False
    """Folders and roles are only fetched for declared workspaces.

    False means "not looked at", never "has none" — `plan()` must not read an empty
    list here as drift.
    """

    folders: list[ObservedFolder] = []
    roles: list[ObservedRole] = []


class Observed(BaseModel):
    workspaces: list[ObservedWorkspace] = []
    capacities: dict[str, str] = {}
    """Capacity display name -> id."""

    identity: str | None = None
    """Entra object id of whoever observed, when the token revealed it.

    Needed at plan time: Fabric makes the creator of a workspace an Admin, so granting
    that same principal again would fail on a workspace this run creates.
    """

    def by_name(self) -> dict[str, ObservedWorkspace]:
        return {w.name: w for w in self.workspaces}
