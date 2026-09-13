"""Phase 5: the second module — watching job health, planning only."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from afabric.kernel.desired import PolicyConfig
from afabric.modules.jobhealth import process
from afabric.modules.jobhealth.model import Config

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
POLICY = PolicyConfig()

ITEMS = [
    {"id": "nb1", "displayName": "nb_bronze", "type": "Notebook"},
    {"id": "nb2", "displayName": "nb_gold", "type": "Notebook"},
    {"id": "pl1", "displayName": "pl_load", "type": "DataPipeline"},
    {"id": "lh1", "displayName": "lakehouse", "type": "Lakehouse"},
]


def _run(item, status, hours_ago, job_type="RunNotebook"):
    start = NOW - timedelta(hours=hours_ago)
    return {
        "id": f"{item}-{hours_ago}",
        "itemId": item,
        "jobType": job_type,
        "invokeType": "Scheduled",
        "status": status,
        "startTimeUtc": start.replace(tzinfo=None).isoformat(),
        "endTimeUtc": (start + timedelta(minutes=1)).replace(tzinfo=None).isoformat(),
        "failureReason": {"errorCode": "Boom"} if status == "Failed" else None,
    }


class FakeBus:
    """Serves the job-scheduler endpoints from canned rows."""

    def __init__(self, runs=None, schedules=None, workspaces=("faf_dev",)):
        self.runs = runs or {}
        self.schedules = schedules or {}
        self.workspaces = workspaces
        self.calls = []

    async def call(self, name, **args):
        self.calls.append((name, args))
        if name == "list_workspaces":
            return [{"id": f"{w}-id", "displayName": w} for w in self.workspaces]
        if name == "list_items":
            return ITEMS
        if name == "list_item_job_instances":
            return self.runs.get(args["itemId"], [])
        if name == "list_item_schedules":
            return self.schedules.get(args["itemId"], [])
        raise AssertionError(f"unexpected tool {name}")


def _config(**overrides):
    spec = {"workspace": "faf_dev", "rerun_failed": True}
    spec.update(overrides)
    return Config.model_validate([spec])


def _observe(bus, desired):
    return asyncio.run(process.observe(bus, desired))


# --- the declaration --------------------------------------------------------------


def test_a_watch_must_watch_something():
    with pytest.raises(ValidationError, match="declares nothing to watch"):
        Config.model_validate([{"workspace": "faf_dev"}])


def test_an_item_type_without_a_job_type_is_refused():
    with pytest.raises(ValidationError, match="no job type known for Lakehouse"):
        Config.model_validate(
            [{"workspace": "faf_dev", "types": ["Lakehouse"], "schedule": "required"}]
        )


# --- observe ----------------------------------------------------------------------


def test_observes_only_matching_schedulable_items():
    bus = FakeBus()
    observed = _observe(bus, _config(items="nb_*"))

    [watch] = observed.watches
    assert [i.name for i in watch.items] == ["nb_bronze", "nb_gold"]
    assert all(i.job_type == "RunNotebook" for i in watch.items)
    # The lakehouse has no job type, the pipeline does not match the glob: neither is asked.
    asked = {args["itemId"] for name, args in bus.calls if name == "list_item_job_instances"}
    assert asked == {"nb1", "nb2"}


def test_schedules_are_only_fetched_when_declared():
    bus = FakeBus()
    _observe(bus, _config(items="nb_bronze"))
    assert not [c for c in bus.calls if c[0] == "list_item_schedules"]

    bus = FakeBus()
    _observe(bus, _config(items="nb_bronze", schedule="required"))
    [(_, args)] = [c for c in bus.calls if c[0] == "list_item_schedules"]
    assert args["jobType"] == "RunNotebook"


def test_a_missing_workspace_is_recorded_and_refuses_planning():
    bus = FakeBus(workspaces=())
    desired = _config()
    observed = _observe(bus, desired)
    assert observed.missing_workspaces == ["faf_dev"]
    with pytest.raises(process.PlanError, match="faf_dev"):
        process.plan(desired, observed, POLICY, now=NOW)


# --- plan -------------------------------------------------------------------------


def test_plans_a_schedule_where_one_is_required_and_missing():
    bus = FakeBus(schedules={"nb1": [{"id": "s1", "enabled": True}]})
    desired = _config(items="nb_*", schedule="required", rerun_failed=False)

    changes = process.plan(desired, _observe(bus, desired), POLICY, now=NOW)

    assert [(c.action, c.target) for c in changes] == [("job.schedule", "faf_dev/nb_gold")]
    assert changes[0].risk.value == "safe"


def test_plans_a_rerun_for_a_failed_newest_run():
    bus = FakeBus(
        runs={
            "nb1": [_run("nb1", "Completed", 5), _run("nb1", "Failed", 1)],
            "nb2": [_run("nb2", "Failed", 9), _run("nb2", "Completed", 2)],
        }
    )
    desired = _config(items="nb_*")

    changes = process.plan(desired, _observe(bus, desired), POLICY, now=NOW)

    assert [(c.action, c.target) for c in changes] == [("job.rerun", "faf_dev/nb_bronze")]
    change = changes[0]
    assert change.risk.value == "reversible"
    assert change.before["status"] == "Failed" and change.metadata["item_id"] == "nb1"


def test_plans_a_rerun_when_the_newest_success_is_too_old():
    bus = FakeBus(
        runs={
            "nb1": [_run("nb1", "Completed", 30)],
            "nb2": [_run("nb2", "Completed", 2)],
        }
    )
    desired = _config(items="nb_*", rerun_failed=False, stale_after_hours=24)

    changes = process.plan(desired, _observe(bus, desired), POLICY, now=NOW)

    assert [c.target for c in changes] == ["faf_dev/nb_bronze"]
    assert "stale_after_hours=24" in changes[0].reason


def test_a_running_job_is_neither_stale_nor_failed():
    bus = FakeBus(runs={"nb1": [_run("nb1", "InProgress", 0)]})
    desired = _config(items="nb_bronze", stale_after_hours=1)
    assert process.plan(desired, _observe(bus, desired), POLICY, now=NOW) == []


def test_an_item_that_never_ran_is_planned_once_stale_is_declared():
    bus = FakeBus()
    desired = _config(items="nb_bronze", rerun_failed=False, stale_after_hours=24)
    [change] = process.plan(desired, _observe(bus, desired), POLICY, now=NOW)
    assert change.reason == "no successful run on record"


def test_nothing_is_planned_when_the_expectation_holds():
    bus = FakeBus(
        runs={"nb1": [_run("nb1", "Completed", 1)]},
        schedules={"nb1": [{"id": "s1"}]},
    )
    desired = _config(items="nb_bronze", schedule="required", stale_after_hours=24)
    assert process.plan(desired, _observe(bus, desired), POLICY, now=NOW) == []


# --- project ----------------------------------------------------------------------


def test_planning_against_the_projection_is_empty():
    bus = FakeBus(runs={"nb1": [_run("nb1", "Failed", 1)]})
    desired = _config(items="nb_bronze", schedule="required", stale_after_hours=24)
    observed = _observe(bus, desired)

    changes = process.plan(desired, observed, POLICY, now=NOW)
    assert {c.action for c in changes} == {"job.schedule", "job.rerun"}

    after = process.project(observed, changes)
    assert process.plan(desired, after, POLICY, now=NOW) == []
    # The projection is a copy; what was observed stays as it was observed.
    assert observed.watches[0].items[0].schedules == []


# --- apply ------------------------------------------------------------------------


def test_apply_refuses_rather_than_half_acting():
    bus = FakeBus(runs={"nb1": [_run("nb1", "Failed", 1)]})
    desired = _config(items="nb_*")
    changes = process.plan(desired, _observe(bus, desired), POLICY, now=NOW)

    outcomes = asyncio.run(process.apply(changes, bus))

    assert len(outcomes) == 1 and not outcomes[0].ok
    assert "not implemented" in outcomes[0].detail
