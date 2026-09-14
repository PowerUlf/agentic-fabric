"""The `jobs:` section of fabric.yaml, and the observed run history it is compared to."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

# Which job type the scheduler uses for an item type. The schedules endpoint rejects a
# wrong pairing with 400, so a type this module does not know is skipped rather than
# guessed at.
JOB_TYPES: dict[str, str] = {
    "Notebook": "RunNotebook",
    "DataPipeline": "Pipeline",
    "SparkJobDefinition": "sparkjob",
    "Dataflow": "Refresh",
}

class ScheduleSpec(BaseModel):
    """A schedule to create where an item has none.

    Only Fabric's `Cron` shape: every `interval_minutes`, between two moments. The daily,
    weekly and monthly shapes exist in the API and are not declared here yet.
    """

    model_config = ConfigDict(extra="forbid")

    interval_minutes: int = Field(ge=1, le=5_270_400)
    """1 minute to 10 years, the range the API accepts."""

    timezone: str = "UTC"
    """Windows time zone id, e.g. `W. Europe Standard Time`."""

    start: datetime | None = None
    """When it first runs. Default: an hour from the moment apply runs.

    A start in the past triggers a run immediately — the API says so — which is not what
    declaring a schedule usually means.
    """

    end: datetime | None = None
    """When it stops. Default: a year after `start`."""

    enabled: bool = True

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> ScheduleSpec:
        if self.start and self.end and self.end <= self.start:
            raise ValueError("end must be later than start")
        return self

    def configuration(self, now: datetime) -> dict[str, Any]:
        """The `CronScheduleConfig` body, with the defaults filled in."""
        start = self.start or now + timedelta(hours=1)
        end = self.end or start + timedelta(days=365)
        return {
            "type": "Cron",
            "interval": self.interval_minutes,
            "localTimeZoneId": self.timezone,
            "startDateTime": _stamp(start),
            "endDateTime": _stamp(end),
        }


def _stamp(moment: datetime) -> str:
    """Fabric wants `YYYY-MM-DDTHH:mm:ss`, no offset, understood as the given zone."""
    return moment.replace(tzinfo=None, microsecond=0).isoformat()


# --- desired ----------------------------------------------------------------------


class WatchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace: str = Field(min_length=1)
    """Workspace display name. Must exist; this module never creates one."""

    items: str = "*"
    """Glob over item display names, e.g. `nb_*`. `*` watches every eligible item."""

    types: list[str] = Field(default_factory=lambda: sorted(JOB_TYPES))
    """Item types to watch. Anything outside `JOB_TYPES` has no schedulable job."""

    schedule: ScheduleSpec | None = None
    """Declared: every watched item without a schedule gets this one."""

    rerun_failed: bool = False
    """Plan a rerun when an item's newest run failed."""

    stale_after_hours: int | None = Field(default=None, ge=1)
    """Plan a rerun when the newest successful run is older than this."""

    @model_validator(mode="after")
    def _watches_something(self) -> WatchSpec:
        if self.schedule is None and not self.rerun_failed and self.stale_after_hours is None:
            raise ValueError(
                "declares nothing to watch — set schedule, rerun_failed or stale_after_hours"
            )
        unknown = [t for t in self.types if t not in JOB_TYPES]
        if unknown:
            known = ", ".join(sorted(JOB_TYPES))
            raise ValueError(f"no job type known for {', '.join(unknown)} (known: {known})")
        return self


class Config(RootModel[list[WatchSpec]]):
    """Validated `jobs:` section."""

    @model_validator(mode="after")
    def _unique_watches(self) -> Config:
        seen = [(w.workspace, w.items) for w in self.root]
        duplicates = sorted({f"{w}/{i}" for w, i in seen if seen.count((w, i)) > 1})
        if duplicates:
            raise ValueError(f"the same workspace and item pattern twice: {', '.join(duplicates)}")
        return self

    def __iter__(self):
        return iter(self.root)


# --- observed ---------------------------------------------------------------------


class Run(BaseModel):
    id: str
    job_type: str
    status: str
    """`Completed`, `Failed`, `Cancelled`, `InProgress`, `NotStarted`, `Deduped`."""

    invoke_type: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    failure: dict[str, Any] | None = None


class ObservedItem(BaseModel):
    id: str
    name: str
    type: str
    job_type: str
    runs: list[Run] = []
    schedules: list[dict[str, Any]] = []

    @property
    def newest(self) -> Run | None:
        """The newest run that has started. Runs come back unordered."""
        started = [r for r in self.runs if r.start is not None]
        return max(started, key=lambda r: r.start) if started else None

    def newest_success(self) -> Run | None:
        done = [r for r in self.runs if r.status == "Completed" and r.start is not None]
        return max(done, key=lambda r: r.start) if done else None


class ObservedWatch(BaseModel):
    workspace: str
    workspace_id: str
    items: list[ObservedItem] = []


class Observed(BaseModel):
    watches: list[ObservedWatch] = []
    missing_workspaces: list[str] = []
    """Declared workspaces the tenant does not have. `plan` refuses on these."""
