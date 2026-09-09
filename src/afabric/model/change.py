"""The one currency every module deals in.

Policy, approval, dry-run, the journal and (later) the UI all operate on `Change`.
Keeping a single type is what lets a future data-engineering module inherit every
guardrail the workspace module has, for free.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Risk(str, Enum):
    """How much damage a change can do if it is wrong.

    Policy reasons about this, not about the action verb — so a module can add new
    verbs without teaching the kernel anything.
    """

    SAFE = "safe"
    """Creates something new, or changes nothing observable. No prior state at stake."""

    REVERSIBLE = "reversible"
    """Alters existing state, but `before` holds everything needed to undo it."""

    DESTRUCTIVE = "destructive"
    """Removes state or access. Requires explicit approval regardless of module."""


class Change(BaseModel):
    """A single intended difference between desired and observed state."""

    module: str
    """Name of the module that produced this change."""

    action: str
    """Module-defined verb, e.g. `workspace.create`, `role.revoke`."""

    target: str
    """Human-readable identity of what is being changed. Shown in plans and approvals."""

    risk: Risk

    before: dict[str, Any] | None = None
    """Observed state, absent for creations."""

    after: dict[str, Any] | None = None
    """Desired state, absent for deletions."""

    reason: str | None = None
    """Why this change was proposed. Carried into the journal and the approval prompt."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Module-private payload — resource ids and anything `apply()` needs to act."""

    def summary(self) -> str:
        return f"{self.action} {self.target}"
