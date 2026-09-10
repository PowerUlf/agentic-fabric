"""Module discovery.

A module is found in one of three ways, all yielding the same `Module`:

1. **built-in** — a subpackage of `afabric.modules`
2. **entry point** — the `afabric.modules` group, so an installed package can ship one
3. **module path** — a subdirectory of a directory in `AFABRIC_MODULE_PATH`, loaded
   straight from disk. This is the "a module is a directory" promise: no install, no
   registry edit, no kernel change.

Nothing here is allowed to fail quietly. A module that does not import, a name defined
twice, a capability nobody provides — each becomes a `ModuleProblem` the CLI shows. A
broken module that silently vanishes is how a reconciler ends up planning against half
its desired state.
"""

from __future__ import annotations

import hashlib
import importlib
import pkgutil
import sys
import types
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from types import ModuleType

from afabric.kernel.manifest import ModuleManifest

ENTRY_POINT_GROUP = "afabric.modules"
BUILTIN_PACKAGE = "afabric.modules"

KERNEL_CAPABILITIES: frozenset[str] = frozenset({"fabric.core"})
"""Capabilities the kernel serves itself, through the ToolBus."""

RESERVED_CONFIG_KEYS: frozenset[str] = frozenset({"version", "policy"})
"""Top-level fabric.yaml keys owned by the kernel, never by a module."""


@dataclass(frozen=True)
class ModuleProblem:
    source: str
    """Where the problem was found — a package name, entry point or directory."""

    message: str


@dataclass
class Module:
    manifest: ModuleManifest
    package: str
    """Importable package name. Components are imported relative to it."""

    origin: str
    """How it was found: `builtin`, `entry-point` or the directory path."""

    @property
    def name(self) -> str:
        return self.manifest.name

    def component(self, name: str) -> ModuleType | None:
        """Import one of `model`, `process` or `tools`, or None when absent.

        Only a missing component is tolerated. An import error *inside* an existing
        component propagates, because that is a bug in the module, not an option it
        chose not to use.
        """
        qualified = f"{self.package}.{name}"
        try:
            return importlib.import_module(qualified)
        except ModuleNotFoundError as exc:
            if exc.name == qualified:
                return None
            raise


@dataclass
class ModuleRegistry:
    modules: dict[str, Module] = field(default_factory=dict)
    problems: list[ModuleProblem] = field(default_factory=list)

    def __iter__(self):
        return iter(sorted(self.modules.values(), key=lambda m: m.name))

    def __len__(self) -> int:
        return len(self.modules)

    @property
    def ok(self) -> bool:
        return not self.problems

    def by_config_key(self) -> dict[str, Module]:
        return {m.manifest.config_key: m for m in self if m.manifest.config_key}

    def provided_capabilities(self) -> set[str]:
        provided = set(KERNEL_CAPABILITIES)
        for module in self:
            provided.update(module.manifest.provides)
        return provided


# --- discovery ------------------------------------------------------------------


def discover(module_dirs: list[Path] | None = None) -> ModuleRegistry:
    registry = ModuleRegistry()

    for package, origin in _builtin_packages():
        _admit(registry, package, origin)

    for ep in entry_points(group=ENTRY_POINT_GROUP):
        _admit_entry_point(registry, ep)

    for directory in module_dirs or []:
        for package, origin in _directory_packages(registry, directory):
            _admit(registry, package, origin)

    _check_consistency(registry)
    return registry


def _builtin_packages():
    root = importlib.import_module(BUILTIN_PACKAGE)
    for info in pkgutil.iter_modules(root.__path__):
        if info.ispkg:
            yield f"{BUILTIN_PACKAGE}.{info.name}", "builtin"


def _admit_entry_point(registry: ModuleRegistry, ep) -> None:
    source = f"entry point {ep.name!r} ({ep.value})"
    try:
        manifest = ep.load()
    except Exception as exc:
        registry.problems.append(ModuleProblem(source, f"failed to load: {exc}"))
        return

    # `afabric.modules.workspace.manifest:MANIFEST` -> `afabric.modules.workspace`
    package = ep.module.rpartition(".")[0]
    _register(registry, manifest, package, "entry-point", source)


def _directory_packages(registry: ModuleRegistry, directory: Path):
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        registry.problems.append(
            ModuleProblem(str(directory), "module path entry is not a directory")
        )
        return

    # The synthetic parent name embeds a hash of the directory, so two module-path
    # entries — or two test runs on different temp dirs — never share sys.modules.
    parent = f"afabric_ext_{hashlib.sha1(str(directory).encode()).hexdigest()[:10]}"
    if parent not in sys.modules:
        namespace = types.ModuleType(parent)
        namespace.__path__ = [str(directory)]
        sys.modules[parent] = namespace

    for child in sorted(directory.iterdir()):
        if child.is_dir() and (child / "manifest.py").is_file():
            if not child.name.isidentifier():
                registry.problems.append(
                    ModuleProblem(str(child), "module directory name must be a valid identifier")
                )
                continue
            yield f"{parent}.{child.name}", str(child)


def _admit(registry: ModuleRegistry, package: str, origin: str) -> None:
    source = package if origin == "builtin" else origin
    try:
        manifest_module = importlib.import_module(f"{package}.manifest")
    except Exception as exc:
        registry.problems.append(ModuleProblem(source, f"manifest failed to import: {exc}"))
        return

    manifest = getattr(manifest_module, "MANIFEST", None)
    _register(registry, manifest, package, origin, source)


def _register(
    registry: ModuleRegistry, manifest: object, package: str, origin: str, source: str
) -> None:
    if not isinstance(manifest, ModuleManifest):
        registry.problems.append(
            ModuleProblem(source, "does not define MANIFEST as a ModuleManifest")
        )
        return

    existing = registry.modules.get(manifest.name)
    if existing is not None:
        # A built-in module is also listed as an entry point. Same package, same module.
        if existing.package == package:
            return
        registry.problems.append(
            ModuleProblem(
                source,
                f"module {manifest.name!r} is already defined by {existing.origin} "
                f"({existing.package})",
            )
        )
        return

    registry.modules[manifest.name] = Module(manifest, package, origin)


def _check_consistency(registry: ModuleRegistry) -> None:
    config_owner: dict[str, str] = {}
    capability_owner: dict[str, str] = {}

    # Discovery order, not alphabetical: built-ins were admitted first and keep what they
    # claim. Sorting by name would let an out-of-tree module called "aaa" take a config
    # key from a built-in and get the built-in reported as the offender.
    for module in registry.modules.values():
        key = module.manifest.config_key
        if key in RESERVED_CONFIG_KEYS:
            registry.problems.append(
                ModuleProblem(module.name, f"config key {key!r} is reserved for the kernel")
            )
        elif key and key in config_owner:
            registry.problems.append(
                ModuleProblem(
                    module.name, f"config key {key!r} is already owned by {config_owner[key]!r}"
                )
            )
        elif key:
            config_owner[key] = module.name

        for capability in module.manifest.provides:
            if capability in KERNEL_CAPABILITIES:
                registry.problems.append(
                    ModuleProblem(
                        module.name, f"capability {capability!r} is provided by the kernel"
                    )
                )
            elif capability in capability_owner:
                registry.problems.append(
                    ModuleProblem(
                        module.name,
                        f"capability {capability!r} is already provided by "
                        f"{capability_owner[capability]!r}",
                    )
                )
            else:
                capability_owner[capability] = module.name

    provided = registry.provided_capabilities()
    for module in registry:
        for capability in module.manifest.requires:
            if capability not in provided:
                registry.problems.append(
                    ModuleProblem(
                        module.name, f"requires {capability!r}, which nothing provides"
                    )
                )
