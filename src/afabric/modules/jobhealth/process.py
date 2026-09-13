"""Watch job runs, and plan what a declared health expectation would take.

Same three-way split as the workspace module: `observe` does the I/O, `plan` and
`project` are pure. `apply` is deliberately not implemented yet — this module reads and
plans, so that `afab plan` is a health report you can trust before anything of it can
act.

The job scheduler's endpoints are per item and per job type, and the job type follows
from the item type (`model.JOB_TYPES`). A wrong pairing is a 400, so items whose type
this module does not know are skipped rather than guessed at.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from fnmatch import fnmatchcase
from typing import Any

from afabric.kernel.desired import PolicyConfig
from afabric.model.change import Change, Outcome, Risk
from afabric.modules.jobhealth.model import (
    JOB_TYPES,
    Config,
    Observed,
    ObservedItem,
    ObservedWatch,
    Run,
    WatchSpec,
)

MODULE = "job-health"

# Runs that are still going. Neither a failure nor a success to reason about.
RUNNING = {"NotStarted", "InProgress"}


class PlanError(ValueError):
    """The desired state cannot be planned against this tenant at all."""


# --- observe ----------------------------------------------------------------------


async def observe(bus, desired: Config) -> Observed:
    workspaces = {w["displayName"]: w["id"] for w in _rows(await bus.call("list_workspaces"))}
    observed = Observed()

    for watch in desired:
        workspace_id = workspaces.get(watch.workspace)
        if workspace_id is None:
            observed.missing_workspaces.append(watch.workspace)
            continue

        found = ObservedWatch(workspace=watch.workspace, workspace_id=workspace_id)
        for row in _rows(await bus.call("list_items", workspaceId=workspace_id)):
            item_type = row.get("type", "")
            if item_type not in watch.types or item_type not in JOB_TYPES:
                continue
            if not fnmatchcase(row["displayName"], watch.items):
                continue

            item = ObservedItem(
                id=row["id"],
                name=row["displayName"],
                type=item_type,
                job_type=JOB_TYPES[item_type],
            )
            item.runs = [
                _run(instance)
                for instance in _rows(
                    await bus.call(
                        "list_item_job_instances", workspaceId=workspace_id, itemId=item.id
                    )
                )
            ]
            if watch.schedule != "ignore":
                item.schedules = _rows(
                    await bus.call(
                        "list_item_schedules",
                        workspaceId=workspace_id,
                        itemId=item.id,
                        jobType=item.job_type,
                    )
                )
            found.items.append(item)
        observed.watches.append(found)

    return observed


def _run(instance: dict[str, Any]) -> Run:
    return Run(
        id=instance["id"],
        job_type=instance.get("jobType", ""),
        status=instance.get("status", ""),
        invoke_type=instance.get("invokeType"),
        start=_moment(instance.get("startTimeUtc")),
        end=_moment(instance.get("endTimeUtc")),
        failure=instance.get("failureReason"),
    )


def _moment(value: str | None) -> datetime | None:
    """Parse a Fabric timestamp. They carry no offset and are documented as UTC."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        payload = payload.get("value", [])
    return [row for row in payload or [] if isinstance(row, dict)]


# --- plan -------------------------------------------------------------------------


def plan(desired: Config, observed: Observed, policy: PolicyConfig, now=None) -> list[Change]:
    if observed.missing_workspaces:
        raise PlanError(
            "workspace(s) not found: " + ", ".join(sorted(set(observed.missing_workspaces)))
        )

    now = now or datetime.now(UTC)
    by_workspace = {w.workspace: w for w in observed.watches}
    changes: list[Change] = []

    for watch in desired:
        found = by_workspace.get(watch.workspace)
        if found is None:
            continue
        for item in found.items:
            changes.extend(_for_item(watch, found, item, now))

    return changes


def _for_item(watch: WatchSpec, found: ObservedWatch, item: ObservedItem, now) -> list[Change]:
    changes: list[Change] = []
    target = f"{watch.workspace}/{item.name}"
    metadata = {
        "workspace_id": found.workspace_id,
        "item_id": item.id,
        "job_type": item.job_type,
    }

    if watch.schedule == "required" and not item.schedules:
        changes.append(
            Change(
                module=MODULE,
                action="job.schedule",
                target=target,
                risk=Risk.SAFE,
                after={"schedule": "required", "job_type": item.job_type},
                reason="declared as required, item has no schedule",
                metadata=metadata,
            )
        )

    newest = item.newest
    if watch.rerun_failed and newest is not None and newest.status == "Failed":
        return changes + [_rerun(target, item, newest, metadata, "newest run failed")]

    if watch.stale_after_hours is not None and (newest is None or newest.status not in RUNNING):
        success = item.newest_success()
        cutoff = now - timedelta(hours=watch.stale_after_hours)
        if success is None:
            changes.append(
                _rerun(target, item, newest, metadata, "no successful run on record")
            )
        elif success.start < cutoff:
            age = int((now - success.start).total_seconds() // 3600)
            changes.append(
                _rerun(
                    target,
                    item,
                    newest,
                    metadata,
                    f"newest success is {age}h old, older than stale_after_hours="
                    f"{watch.stale_after_hours}",
                )
            )

    return changes


def _rerun(target, item: ObservedItem, newest: Run | None, metadata: dict, reason: str) -> Change:
    return Change(
        module=MODULE,
        action="job.rerun",
        target=target,
        # Rerunning writes whatever the job writes. Reversible is the honest floor:
        # `before` cannot capture what the job will overwrite.
        risk=Risk.REVERSIBLE,
        before=(
            {"status": newest.status, "started": newest.start.isoformat() if newest.start else None}
            if newest
            else None
        ),
        after={"run": "requested"},
        reason=reason,
        metadata={**metadata, "last_run_id": newest.id if newest else None},
    )


# --- project ----------------------------------------------------------------------


def project(observed: Observed, changes: list[Change]) -> Observed:
    """What the tenant would look like afterwards, assuming every change succeeded."""
    after = observed.model_copy(deep=True)
    items = {
        (watch.workspace_id, item.id): item for watch in after.watches for item in watch.items
    }

    for change in changes:
        item = items.get((change.metadata.get("workspace_id"), change.metadata.get("item_id")))
        if item is None:
            continue
        if change.action == "job.schedule":
            item.schedules = [{"enabled": True, "projected": True}]
        elif change.action == "job.rerun":
            # A requested run is assumed to succeed; planning against this state must
            # therefore be empty, which is what idempotency means here. It starts after
            # every run observed rather than at the wall clock, so the projection does
            # not depend on when it is computed.
            started = [r.start for r in item.runs if r.start is not None]
            item.runs.append(
                Run(
                    id=f"projected-{item.id}",
                    job_type=item.job_type,
                    status="Completed",
                    invoke_type="Manual",
                    start=max(started) + timedelta(seconds=1) if started else datetime.now(UTC),
                )
            )

    return after


# --- apply ------------------------------------------------------------------------


async def apply(changes: list[Change], bus) -> list[Outcome]:
    """Not implemented: this module reads and plans.

    Creating a schedule or starting a run writes to the tenant, and neither has been
    verified live. Refusing here keeps `afab apply` honest rather than half-acting.
    """
    return [
        Outcome(
            change=change,
            ok=False,
            detail="job-health plans only; applying a job change is not implemented yet",
        )
        for change in changes[:1]
    ]
