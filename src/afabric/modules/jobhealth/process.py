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
            if watch.schedule is not None:
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
            changes.extend(_for_item(watch, found, item, now, policy))

    return changes


def _for_item(
    watch: WatchSpec, found: ObservedWatch, item: ObservedItem, now, policy
) -> list[Change]:
    changes: list[Change] = []
    target = f"{watch.workspace}/{item.name}"
    metadata = {
        "workspace_id": found.workspace_id,
        "item_id": item.id,
        "job_type": item.job_type,
    }

    changes.extend(_schedules(watch, item, target, metadata, now, policy))

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


def _schedules(watch: WatchSpec, item: ObservedItem, target, metadata, now, policy) -> list[Change]:
    """Line the declared schedules up against the existing ones, oldest first.

    Fabric schedules have no name, only an id and a creation time, so position is the
    only stable pairing available: the first declared one is the oldest existing one.
    """
    declared = watch.schedules
    if not declared:
        return []

    existing = sorted(item.schedules, key=lambda s: s.get("createdDateTime") or "")
    changes: list[Change] = []

    for index, spec in enumerate(declared):
        if index < len(existing):
            changes.extend(_schedule_update(spec, existing[index], target, metadata))
        else:
            changes.append(_schedule_create(spec, target, metadata, now))

    if policy.prune:
        for surplus in existing[len(declared) :]:
            changes.append(
                Change(
                    module=MODULE,
                    action="job.schedule_delete",
                    target=target,
                    risk=Risk.DESTRUCTIVE,
                    before={
                        "enabled": surplus.get("enabled"),
                        "configuration": surplus.get("configuration"),
                    },
                    reason=f"{len(existing)} schedules exist, {len(declared)} declared, "
                    "and policy.prune is on",
                    metadata={**metadata, "schedule_id": surplus.get("id")},
                )
            )

    return changes


def _schedule_create(spec, target, metadata: dict, now) -> Change:
    configuration = spec.configuration(now)
    return Change(
        module=MODULE,
        action="job.schedule",
        target=target,
        risk=Risk.SAFE,
        after={
            "every_minutes": spec.interval_minutes,
            "from": configuration["startDateTime"],
            "until": configuration["endDateTime"],
            "timezone": spec.timezone,
            "enabled": spec.enabled,
        },
        reason="declared, item has no schedule for it",
        # apply sends exactly what plan showed: the body is decided here, not recomputed
        # later from a clock that has moved on.
        metadata={**metadata, "configuration": configuration, "enabled": spec.enabled},
    )


def _schedule_update(spec, schedule: dict, target, metadata: dict) -> list[Change]:
    differences = spec.differences(schedule)
    if not differences:
        return []
    # Fabric disables a scheduler itself after repeated failures.
    disabled = differences.get("enabled") == (False, True)
    return [
        Change(
            module=MODULE,
            action="job.schedule_update",
            target=target,
            risk=Risk.REVERSIBLE,
            before={field: was for field, (was, _) in differences.items()},
            after={field: wanted for field, (_, wanted) in differences.items()},
            reason=(
                "schedule is disabled — declared enabled; Fabric disables a scheduler "
                "after about ten consecutive failures"
                if disabled
                else "schedule differs from the declaration"
            ),
            metadata={
                **metadata,
                "schedule_id": schedule.get("id"),
                "configuration": spec.update(schedule),
                "enabled": spec.enabled,
            },
        )
    ]


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
            # Newest, so the age order the plan pairs by stays intact.
            item.schedules.append(
                {
                    "id": f"projected-{len(item.schedules)}",
                    "createdDateTime": "9999",
                    "enabled": change.metadata["enabled"],
                    "configuration": change.metadata["configuration"],
                }
            )
        elif change.action == "job.schedule_update":
            for schedule in item.schedules:
                if schedule.get("id") == change.metadata["schedule_id"]:
                    schedule["enabled"] = change.metadata["enabled"]
                    schedule["configuration"] = change.metadata["configuration"]
        elif change.action == "job.schedule_delete":
            item.schedules = [
                s for s in item.schedules if s.get("id") != change.metadata["schedule_id"]
            ]
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
    """Create the declared schedules and start the planned runs, in plan order.

    Stops at the first failure: a later change may well depend on an earlier one, and
    the runner reports what was not attempted.
    """
    outcomes: list[Outcome] = []
    for change in changes:
        try:
            outcomes.append(await _apply_one(change, bus))
        except Exception as exc:
            outcomes.append(
                Outcome(change=change, ok=False, detail=f"{type(exc).__name__}: {exc}")
            )
        if not outcomes[-1].ok:
            break
    return outcomes


async def _apply_one(change: Change, bus) -> Outcome:
    where = {
        "workspaceId": change.metadata["workspace_id"],
        "itemId": change.metadata["item_id"],
        "jobType": change.metadata["job_type"],
    }

    if change.action == "job.schedule":
        body = await bus.call(
            "create_item_schedule",
            **where,
            enabled=change.metadata["enabled"],
            configuration=change.metadata["configuration"],
        )
        schedule_id = (body or {}).get("id")
        return Outcome(change=change, ok=True, output={"schedule_id": schedule_id})

    if change.action == "job.schedule_update":
        await bus.call(
            "update_item_schedule",
            **where,
            scheduleId=change.metadata["schedule_id"],
            enabled=change.metadata["enabled"],
            configuration=change.metadata["configuration"],
        )
        return Outcome(change=change, ok=True, output={})

    if change.action == "job.schedule_delete":
        await bus.call("delete_item_schedule", **where, scheduleId=change.metadata["schedule_id"])
        return Outcome(change=change, ok=True, output={})

    if change.action == "job.rerun":
        # 202 with a Location header and no body: started is all the API promises. The
        # run's outcome shows up in `list_item_job_instances` on the next plan.
        await bus.call("run_item_job", **where)
        return Outcome(change=change, ok=True, detail="run started", output={})

    return Outcome(change=change, ok=False, detail=f"unknown action {change.action!r}")
