"""Plan and apply across every module that has something declared.

The runner is where the kernel's pieces meet — modules, desired state, policy, journal —
and it stays use-case free: it hands each module its own section, collects `Change`s,
and routes approved changes back to the module that produced them.

It lives here rather than in the CLI so that anything else driving the system later (the
agent loop, a scheduled service) plans, gates and journals the same way.
"""

from __future__ import annotations

from dataclasses import dataclass

from afabric.kernel.desired import DesiredState
from afabric.kernel.journal import Journal
from afabric.kernel.modules import ModuleRegistry
from afabric.kernel.policy import Evaluation, evaluate
from afabric.model.change import Change, Outcome


@dataclass
class Run:
    changes: list[Change]
    evaluation: Evaluation


async def plan(bus, registry: ModuleRegistry, desired: DesiredState, journal: Journal) -> Run:
    changes: list[Change] = []
    for key, module in registry.by_config_key().items():
        if key not in desired.sections:
            continue
        process = module.component("process")
        if process is None:
            raise RuntimeError(
                f"module {module.name!r} owns {key!r}, which is declared, but has no process"
            )
        observed = await process.observe(bus, desired.sections[key])
        changes.extend(process.plan(desired.sections[key], observed, desired.policy))

    evaluation = evaluate(changes, desired.policy, bus.identity)
    for verdict in evaluation.verdicts:
        journal.record(
            "planned",
            verdict.change,
            denied=verdict.denied,
            needs_approval=verdict.needs_approval,
            reasons=verdict.reasons,
        )
    if evaluation.refused:
        journal.record("refused", reason=evaluation.refused)
    return Run(changes, evaluation)


async def apply(
    bus, registry: ModuleRegistry, approved: list[Change], journal: Journal
) -> list[Outcome]:
    """Apply approved changes module by module, in plan order, stopping at a failure."""
    outcomes: list[Outcome] = []

    for change in approved:
        journal.record("approved", change)

    for module_name, batch in _batches(approved):
        process = registry.modules[module_name].component("process")
        results = await process.apply(batch, bus)
        for outcome in results:
            journal.record(
                "applied" if outcome.ok else "failed",
                outcome.change,
                detail=outcome.detail,
                output=outcome.output,
            )
        outcomes.extend(results)
        if not all(o.ok for o in results) or len(results) < len(batch):
            break

    return outcomes


def _batches(changes: list[Change]) -> list[tuple[str, list[Change]]]:
    """Consecutive runs of changes from the same module, preserving plan order."""
    batches: list[tuple[str, list[Change]]] = []
    for change in changes:
        if batches and batches[-1][0] == change.module:
            batches[-1][1].append(change)
        else:
            batches.append((change.module, [change]))
    return batches
