"""Guardrails, applied identically to every module's changes.

Policy reads only what every `Change` carries — module, action, target, risk, and the
`principal` metadata convention — so it guards modules that do not exist yet.

Three outcomes per change, from strictest to loosest:

- **denied** — never applied, no flag or prompt can override it
- **needs approval** — applied only after explicit, per-change consent
- **allowed** — applied with the run's general confirmation

And one for the run as a whole: **refused**, when it exceeds the blast radius. A run
that is too big is refused entirely rather than trimmed, because a trimmed run applies
an arbitrary subset of the desired state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

from afabric.kernel.desired import DenyRule, PolicyConfig
from afabric.model.change import Change, Risk


@dataclass
class Verdict:
    change: Change
    denied: bool = False
    needs_approval: bool = False
    reasons: list[str] = field(default_factory=list)


@dataclass
class Evaluation:
    verdicts: list[Verdict]
    refused: str | None = None
    """Why the whole run may not proceed, or None."""

    @property
    def denied(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.denied]

    @property
    def gated(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.needs_approval and not v.denied]

    @property
    def allowed(self) -> list[Verdict]:
        return [v for v in self.verdicts if not v.denied and not v.needs_approval]


def evaluate(changes: list[Change], policy: PolicyConfig, identity: str | None) -> Evaluation:
    verdicts = [_judge(change, policy, identity) for change in changes]

    refused = None
    if len(changes) > policy.max_blast_radius:
        refused = (
            f"{len(changes)} changes exceed policy.max_blast_radius "
            f"({policy.max_blast_radius}); nothing will be applied"
        )
    return Evaluation(verdicts, refused)


def _judge(change: Change, policy: PolicyConfig, identity: str | None) -> Verdict:
    verdict = Verdict(change)

    for rule in policy.deny:
        if _matches(rule, change):
            verdict.denied = True
            verdict.reasons.append(f"matches deny rule {rule.model_dump(exclude_none=True)}")

    # Modules put the affected identity in metadata["principal"]. A change that alters or
    # removes an *existing* assignment for the caller (it has a `before`) can lock the
    # caller out — revoking their Admin role, or demoting it. Granting to oneself is
    # harmless and not caught.
    principal = change.metadata.get("principal")
    if principal is not None and change.before is not None:
        if identity is None:
            verdict.needs_approval = True
            verdict.reasons.append(
                "changes an existing assignment, and the caller's identity is unknown — "
                "cannot rule out that this is your own access"
            )
        elif str(principal) == identity:
            verdict.denied = True
            verdict.reasons.append("would change your own access; refused regardless of policy")

    if change.risk is Risk.DESTRUCTIVE:
        verdict.needs_approval = True
        verdict.reasons.append("destructive")
    if change.action in policy.require_approval:
        verdict.needs_approval = True
        verdict.reasons.append(f"{change.action} is listed in policy.require_approval")

    return verdict


def _matches(rule: DenyRule, change: Change) -> bool:
    checks = [
        (rule.module, change.module),
        (rule.action, change.action),
        (rule.target, change.target),
    ]
    return all(pattern is None or fnmatchcase(value, pattern) for pattern, value in checks)
