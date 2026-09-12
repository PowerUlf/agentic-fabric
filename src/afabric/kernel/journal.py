"""Append-only audit journal.

One JSON object per line: what was planned, what policy said, what was approved, what
was applied and how it went. Lines are only ever appended — the journal is the record of
what the system did, so rewriting it would defeat its purpose.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from afabric.model.change import Change


class Journal:
    def __init__(self, path: Path | str, *, run_id: str | None = None) -> None:
        self.path = Path(path)
        self.run_id = run_id or uuid.uuid4().hex[:12]

    def record(self, event: str, change: Change | None = None, **detail: Any) -> None:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "run": self.run_id,
            "event": event,
        }
        if change is not None:
            entry["change"] = change.model_dump(mode="json")
        if detail:
            entry["detail"] = detail

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read(path: Path | str) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
