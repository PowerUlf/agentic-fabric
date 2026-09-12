"""Desired state: fabric.yaml plus the fabric.d/ cascade.

Loading happens in three passes, and the order is what keeps the kernel ignorant of
use cases:

1. **Read.** `fabric.yaml`, then `fabric.d/*.yaml` in filename order. Every top-level
   key is split into `Fragment`s that remember which file they came from.
2. **Merge.** Kernel keys (`version`, `policy`) merge here. Every other key belongs to
   exactly one module, which merges its own fragments — only the workspace module
   knows that workspaces are identified by name.
3. **Validate.** Each module validates only its own section.

All problems across all files are collected and raised together. Fixing a config one
error per run is miserable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, model_validator

from afabric.kernel.modules import RESERVED_CONFIG_KEYS, ModuleRegistry

SUPPORTED_VERSIONS = frozenset({1})
CASCADE_DIR = "fabric.d"


class DenyRule(BaseModel):
    """Changes matching every given field are never applied, whatever the desired state says.

    Fields are shell-style globs over what every `Change` has — the kernel knows no
    module's vocabulary, so a rule cannot say "workspace", only "target".
    """

    model_config = ConfigDict(extra="forbid")

    module: str | None = None
    action: str | None = None
    target: str | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> DenyRule:
        if self.module is None and self.action is None and self.target is None:
            raise ValueError("a deny rule needs at least one of module, action, target")
        return self


class PolicyConfig(BaseModel):
    """The kernel-owned `policy` block. Applies to every module alike."""

    model_config = ConfigDict(extra="forbid")

    max_blast_radius: int = 25
    """Refuse a run that would produce more changes than this."""

    require_approval: list[str] = []
    """Action verbs that need approval even when not destructive."""

    deny: list[DenyRule] = []
    """Changes matching any of these are refused outright."""

    prune: bool = False
    """Whether modules may remove what the desired state does not declare.

    Off by default, and deliberately so: the first run against an existing tenant
    declares almost nothing, and with pruning on it would propose deleting everything
    else. Destructive changes still need approval when this is on.
    """


@dataclass(frozen=True)
class Fragment:
    """One top-level key's value, as written in one file."""

    source: Path
    data: Any


@dataclass
class DesiredState:
    version: int
    policy: PolicyConfig
    sections: dict[str, Any] = field(default_factory=dict)
    """config_key -> the owning module's validated configuration."""

    sources: list[Path] = field(default_factory=list)


class DesiredStateError(ValueError):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


# --- generic merge ----------------------------------------------------------------


def deep_merge(base: Any, overlay: Any) -> Any:
    """Merge the way conf.d users expect when a module has no opinion.

    Mappings merge recursively, lists concatenate, anything else is replaced by the
    later file.
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = deep_merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(base, list) and isinstance(overlay, list):
        return [*base, *overlay]
    return overlay


def merge_fragments(fragments: list[Fragment]) -> Any:
    merged: Any = None
    for fragment in fragments:
        merged = fragment.data if merged is None else deep_merge(merged, fragment.data)
    return merged


# --- loading ----------------------------------------------------------------------


def cascade_files(path: Path) -> list[Path]:
    cascade = path.parent / CASCADE_DIR
    extra = []
    if cascade.is_dir():
        extra = sorted(
            p for p in cascade.iterdir() if p.is_file() and p.suffix in {".yaml", ".yml"}
        )
    return [path, *extra]


def load_desired(path: Path, registry: ModuleRegistry) -> DesiredState:
    path = Path(path)
    if not path.is_file():
        raise DesiredStateError([f"{path}: desired state file not found"])

    problems: list[str] = []
    files = cascade_files(path)
    fragments: dict[str, list[Fragment]] = {}

    for file in files:
        try:
            content = yaml.safe_load(file.read_text()) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{file}: not valid YAML: {exc}")
            continue
        if not isinstance(content, dict):
            problems.append(f"{file}: top level must be a mapping, got {type(content).__name__}")
            continue
        for key, value in content.items():
            fragments.setdefault(str(key), []).append(Fragment(file, value))

    owners = registry.by_config_key()
    for key, found in fragments.items():
        if key not in RESERVED_CONFIG_KEYS and key not in owners:
            known = ", ".join(sorted(RESERVED_CONFIG_KEYS | owners.keys()))
            where = ", ".join(str(f.source) for f in found)
            problems.append(
                f"{where}: unknown top-level key {key!r} — no loaded module owns it "
                f"(known keys: {known})"
            )

    version = _merge_version(fragments.get("version", []), problems)
    policy = _merge_policy(fragments.get("policy", []), problems)

    sections: dict[str, Any] = {}
    for key, module in owners.items():
        if key not in fragments:
            continue
        try:
            sections[key] = _module_section(module, fragments[key])
        except ValidationError as exc:
            for error in exc.errors():
                location = ".".join(str(part) for part in error["loc"])
                problems.append(f"{key}{'.' + location if location else ''}: {error['msg']}")
        except DesiredStateError as exc:
            problems.extend(exc.problems)
        except ValueError as exc:
            problems.append(f"{key}: {exc}")

    if problems:
        raise DesiredStateError(problems)

    return DesiredState(version=version, policy=policy, sections=sections, sources=files)


def _merge_version(found: list[Fragment], problems: list[str]) -> int:
    if not found:
        return 1
    values = {f.data for f in found}
    if len(values) > 1:
        problems.append(
            "version: files disagree — "
            + ", ".join(f"{f.source} says {f.data!r}" for f in found)
        )
        return 1
    (value,) = values
    if value not in SUPPORTED_VERSIONS:
        problems.append(f"version: {value!r} is not supported (supported: 1)")
        return 1
    return value


def _merge_policy(found: list[Fragment], problems: list[str]) -> PolicyConfig:
    merged = merge_fragments(found) or {}
    try:
        return PolicyConfig.model_validate(merged)
    except ValidationError as exc:
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"])
            problems.append(f"policy.{location}: {error['msg']}")
        return PolicyConfig()


def _module_section(module, found: list[Fragment]) -> Any:
    """Merge and validate one module's section through its `model` component.

    A module's `model.py` may define:

    - `Config` — the type its section validates against (required to validate)
    - `merge(fragments) -> data` — its own merge; the generic one is used otherwise

    A module without `model.py` receives its merged section unvalidated.
    """
    model = module.component("model")
    merge: Callable[[list[Fragment]], Any] = getattr(model, "merge", merge_fragments)
    merged = merge(found)

    config_type = getattr(model, "Config", None)
    if config_type is None:
        return merged
    return TypeAdapter(config_type).validate_python(merged)
