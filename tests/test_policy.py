"""Phase 3: guardrails that hold for every module, judged from `Change` alone."""

import base64
import json

from afabric.kernel.auth import principal_id
from afabric.kernel.desired import DenyRule, PolicyConfig
from afabric.kernel.policy import evaluate
from afabric.model.change import Change, Risk

ME = "11111111-1111-1111-1111-111111111111"
OTHER = "99999999-9999-9999-9999-999999999999"


def change(action="workspace.create", target="team_dev", risk=Risk.SAFE, **fields) -> Change:
    return Change(module="workspace", action=action, target=target, risk=risk, **fields)


def revoke(principal: str) -> Change:
    return change(
        "role.revoke",
        f"team_dev ← {principal}",
        Risk.DESTRUCTIVE,
        before={"role": "Admin"},
        metadata={"principal": principal, "assignment_id": "a1"},
    )


def one(changes, policy=None, identity=ME):
    return evaluate(changes, policy or PolicyConfig(), identity).verdicts[0]


class TestApproval:
    def test_safe_change_is_allowed(self):
        verdict = one([change()])
        assert not verdict.denied and not verdict.needs_approval

    def test_destructive_always_needs_approval(self):
        verdict = one([change("workspace.delete", risk=Risk.DESTRUCTIVE)])
        assert verdict.needs_approval and not verdict.denied
        assert "destructive" in verdict.reasons

    def test_require_approval_gates_a_verb(self):
        policy = PolicyConfig(require_approval=["workspace.update"])
        verdict = one([change("workspace.update", risk=Risk.REVERSIBLE)], policy)
        assert verdict.needs_approval

    def test_require_approval_leaves_other_verbs_alone(self):
        policy = PolicyConfig(require_approval=["workspace.update"])
        assert not one([change()], policy).needs_approval


class TestDeny:
    def test_glob_over_target(self):
        policy = PolicyConfig(deny=[DenyRule(target="Production*")])
        assert one([change(target="Production EU")], policy).denied
        assert not one([change(target="Dev EU")], policy).denied

    def test_every_given_field_must_match(self):
        policy = PolicyConfig(deny=[DenyRule(action="*.delete", target="Production*")])
        assert not one([change(target="Production EU")], policy).denied
        assert one([change("workspace.delete", "Production EU", Risk.DESTRUCTIVE)], policy).denied

    def test_matching_is_case_sensitive(self):
        policy = PolicyConfig(deny=[DenyRule(target="Production*")])
        assert not one([change(target="production")], policy).denied

    def test_denied_is_not_also_gated(self):
        policy = PolicyConfig(deny=[DenyRule(module="workspace")])
        evaluation = evaluate([change("workspace.delete", risk=Risk.DESTRUCTIVE)], policy, ME)
        assert len(evaluation.denied) == 1
        assert evaluation.gated == [] and evaluation.allowed == []


class TestOwnAccess:
    def test_revoking_own_role_is_denied(self):
        verdict = one([revoke(ME)], PolicyConfig(), identity=ME)
        assert verdict.denied
        assert any("your own access" in reason for reason in verdict.reasons)

    def test_demoting_own_role_is_denied(self):
        demote = change(
            "role.update",
            f"team_dev ← {ME}",
            Risk.REVERSIBLE,
            before={"role": "Admin"},
            after={"role": "Viewer"},
            metadata={"principal": ME},
        )
        assert one([demote], identity=ME).denied

    def test_no_policy_setting_lifts_the_block(self):
        permissive = PolicyConfig(max_blast_radius=1000, prune=True)
        assert one([revoke(ME)], permissive, identity=ME).denied

    def test_revoking_someone_else_only_needs_approval(self):
        verdict = one([revoke(OTHER)], identity=ME)
        assert verdict.needs_approval and not verdict.denied

    def test_unknown_identity_gates_rather_than_allows(self):
        update = change(
            "role.update",
            f"team_dev ← {OTHER}",
            Risk.REVERSIBLE,
            before={"role": "Member"},
            after={"role": "Viewer"},
            metadata={"principal": OTHER},
        )
        verdict = one([update], identity=None)
        assert verdict.needs_approval and not verdict.denied

    def test_granting_to_oneself_is_not_caught(self):
        grant = change(
            "role.grant",
            f"team_dev ← {ME}",
            Risk.REVERSIBLE,
            after={"role": "Admin"},
            metadata={"principal": ME},
        )
        assert not one([grant], identity=ME).denied


class TestIdentityFromToken:
    def _token(self, claims: dict) -> str:
        body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return f"header.{body}.signature"

    def test_reads_oid(self):
        assert principal_id(self._token({"oid": ME, "upn": "x@y"})) == ME

    def test_anything_unreadable_is_unknown(self):
        assert principal_id("not-a-jwt") is None
        assert principal_id("a.!!!.c") is None
        assert principal_id(self._token({"upn": "x@y"})) is None


class TestBlastRadius:
    def test_run_over_the_limit_is_refused_whole(self):
        changes = [change(target=f"ws{i}") for i in range(3)]
        evaluation = evaluate(changes, PolicyConfig(max_blast_radius=2), ME)
        assert evaluation.refused and "3 changes" in evaluation.refused
        # Every change is still judged and shown; nothing is trimmed.
        assert len(evaluation.verdicts) == 3

    def test_run_at_the_limit_proceeds(self):
        changes = [change(target=f"ws{i}") for i in range(2)]
        assert evaluate(changes, PolicyConfig(max_blast_radius=2), ME).refused is None
