"""Phase 3: the append-only audit journal."""

from afabric.kernel.journal import Journal, read
from afabric.model.change import Change, Risk

CHANGE = Change(module="workspace", action="workspace.create", target="team_dev", risk=Risk.SAFE)


def test_entries_are_appended_across_runs(tmp_path):
    path = tmp_path / "nested" / "journal.jsonl"
    Journal(path, run_id="first").record("planned", CHANGE, denied=False)
    Journal(path, run_id="second").record("refused", reason="too big")

    entries = read(path)
    assert [(e["run"], e["event"]) for e in entries] == [
        ("first", "planned"),
        ("second", "refused"),
    ]
    assert entries[0]["change"]["action"] == "workspace.create"
    assert entries[0]["detail"] == {"denied": False}
    assert "change" not in entries[1]


def test_existing_lines_are_never_rewritten(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"event": "from before"}\n')
    Journal(path).record("applied", CHANGE)
    lines = path.read_text().splitlines()
    assert lines[0] == '{"event": "from before"}'
    assert len(lines) == 2


def test_missing_journal_reads_as_empty(tmp_path):
    assert read(tmp_path / "nope.jsonl") == []
