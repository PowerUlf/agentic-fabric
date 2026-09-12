"""Phase 3: the runner routes changes between modules, policy and the journal."""

import asyncio
from types import SimpleNamespace

from afabric.kernel import runner
from afabric.kernel.desired import DenyRule, DesiredState, PolicyConfig
from afabric.kernel.journal import Journal, read
from afabric.model.change import Change, Outcome, Risk


def _change(module: str, target: str, risk=Risk.SAFE) -> Change:
    return Change(module=module, action=f"{module}.touch", target=target, risk=risk)


class FakeProcess:
    def __init__(self, name: str, planned: list[Change], fail: str | None = None) -> None:
        self.name, self.planned, self.fail = name, planned, fail
        self.applied: list[str] = []

    async def observe(self, bus, section):
        return {"seen": section}

    def plan(self, section, observed, policy):
        assert observed == {"seen": section}
        return list(self.planned)

    async def apply(self, changes, bus):
        outcomes = []
        for change in changes:
            self.applied.append(change.target)
            ok = change.target != self.fail
            outcomes.append(Outcome(change=change, ok=ok, detail=None if ok else "boom"))
            if not ok:
                break
        return outcomes


def _registry(*processes: FakeProcess):
    modules = {p.name: SimpleNamespace(name=p.name, component=lambda _c, p=p: p) for p in processes}
    return SimpleNamespace(
        modules=modules,
        by_config_key=lambda: {f"{name}s": module for name, module in modules.items()},
    )


def _desired(policy=None, **sections) -> DesiredState:
    return DesiredState(version=1, policy=policy or PolicyConfig(), sections=sections)


BUS = SimpleNamespace(identity="me")


def test_plans_only_declared_sections_and_journals_verdicts(tmp_path):
    alpha = FakeProcess("alpha", [_change("alpha", "a1"), _change("alpha", "a2", Risk.DESTRUCTIVE)])
    beta = FakeProcess("beta", [_change("beta", "b1")])
    journal = Journal(tmp_path / "j.jsonl", run_id="r")
    policy = PolicyConfig(deny=[DenyRule(target="a1")])

    run = asyncio.run(
        runner.plan(BUS, _registry(alpha, beta), _desired(policy, alphas=[1]), journal)
    )

    assert [c.target for c in run.changes] == ["a1", "a2"]
    entries = read(journal.path)
    assert [e["event"] for e in entries] == ["planned", "planned"]
    assert entries[0]["detail"]["denied"] is True
    assert entries[1]["detail"]["needs_approval"] is True


def test_refusal_is_journaled(tmp_path):
    alpha = FakeProcess("alpha", [_change("alpha", f"a{i}") for i in range(3)])
    journal = Journal(tmp_path / "j.jsonl")

    run = asyncio.run(
        runner.plan(
            BUS, _registry(alpha), _desired(PolicyConfig(max_blast_radius=2), alphas=[1]), journal
        )
    )

    assert run.evaluation.refused
    assert read(journal.path)[-1]["event"] == "refused"


def test_apply_keeps_plan_order_and_stops_after_a_failing_module(tmp_path):
    alpha = FakeProcess("alpha", [], fail="a2")
    beta = FakeProcess("beta", [])
    approved = [
        _change("alpha", "a1"),
        _change("alpha", "a2"),
        _change("alpha", "a3"),
        _change("beta", "b1"),
    ]
    journal = Journal(tmp_path / "j.jsonl")

    outcomes = asyncio.run(runner.apply(BUS, _registry(alpha, beta), approved, journal))

    assert [o.ok for o in outcomes] == [True, False]
    assert alpha.applied == ["a1", "a2"] and beta.applied == []
    events = [e["event"] for e in read(journal.path)]
    assert events == ["approved"] * 4 + ["applied", "failed"]


def test_apply_interleaves_modules_in_plan_order(tmp_path):
    alpha, beta = FakeProcess("alpha", []), FakeProcess("beta", [])
    approved = [_change("alpha", "a1"), _change("beta", "b1"), _change("alpha", "a2")]
    order = []
    for process in (alpha, beta):
        original = process.apply

        async def traced(changes, bus, original=original):
            order.extend(c.target for c in changes)
            return await original(changes, bus)

        process.apply = traced

    asyncio.run(runner.apply(BUS, _registry(alpha, beta), approved, Journal(tmp_path / "j.jsonl")))

    assert order == ["a1", "b1", "a2"]
