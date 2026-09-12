"""Phase 2: the fabric.yaml + fabric.d/ cascade."""

from pathlib import Path
from textwrap import dedent

import pytest

from afabric.kernel.desired import DesiredStateError, deep_merge, load_desired
from afabric.kernel.modules import discover

EXAMPLES = Path(__file__).parent.parent / "examples" / "fabric.yaml"


@pytest.fixture(scope="module")
def registry():
    return discover()


def _tree(tmp_path: Path, main: str, **cascade: str) -> Path:
    path = tmp_path / "fabric.yaml"
    path.write_text(dedent(main))
    if cascade:
        (tmp_path / "fabric.d").mkdir()
        for name, body in cascade.items():
            (tmp_path / "fabric.d" / f"{name}.yaml").write_text(dedent(body))
    return path


def _names(state):
    return [w.name for w in state.sections["workspaces"]]


def test_shipped_example_loads(registry):
    state = load_desired(EXAMPLES, registry)
    assert _names(state) == ["Sales Analytics Dev", "Marketing Analytics Dev"]
    assert len(state.sources) == 2
    assert state.policy.prune is False


def test_cascade_is_read_in_filename_order(tmp_path, registry):
    path = _tree(
        tmp_path,
        "workspaces: [{name: a}]\n",
        **{"20-late": "workspaces: [{name: c}]\n", "10-early": "workspaces: [{name: b}]\n"},
    )
    assert _names(load_desired(path, registry)) == ["a", "b", "c"]


def test_later_file_extends_a_workspace_by_name(tmp_path, registry):
    principal_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    principal_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    path = _tree(
        tmp_path,
        f"""
        workspaces:
          - name: sales
            description: base
            folders: [Bronze, Silver]
            roles:
              - {{principal: {principal_a}, role: Viewer}}
        """,
        team=f"""
        workspaces:
          - name: sales
            description: overridden
            folders: [Silver, Gold]
            roles:
              - {{principal: {principal_a}, role: Contributor}}
              - {{principal: {principal_b}, role: Viewer}}
        """,
    )
    (sales,) = load_desired(path, registry).sections["workspaces"]

    assert sales.description == "overridden"
    assert sales.folders == ["Bronze", "Silver", "Gold"]
    roles = {str(r.principal): r.role for r in sales.roles}
    assert roles == {principal_a: "Contributor", principal_b: "Viewer"}


def test_name_twice_in_one_file_is_an_error(tmp_path, registry):
    path = _tree(tmp_path, "workspaces: [{name: a}, {name: a}]\n")
    with pytest.raises(DesiredStateError, match="declared twice"):
        load_desired(path, registry)


def test_unknown_top_level_key_names_the_file_and_known_keys(tmp_path, registry):
    path = _tree(tmp_path, "workspace: [{name: a}]\n")  # singular — a typo
    with pytest.raises(DesiredStateError) as exc:
        load_desired(path, registry)
    message = str(exc.value)
    assert "unknown top-level key 'workspace'" in message
    assert "workspaces" in message
    assert str(path) in message


def test_policy_typo_is_caught(tmp_path, registry):
    path = _tree(tmp_path, "policy: {max_blast_radus: 3}\n")
    with pytest.raises(DesiredStateError, match="max_blast_radus"):
        load_desired(path, registry)


def test_deny_rule_needs_a_field(tmp_path, registry):
    path = _tree(tmp_path, "policy: {deny: [{}]}\n")
    with pytest.raises(DesiredStateError, match="at least one of module, action, target"):
        load_desired(path, registry)


def test_deny_rule_rejects_module_vocabulary(tmp_path, registry):
    # The kernel knows no workspaces; `workspace:` was the pre-phase-3 form.
    path = _tree(tmp_path, "policy: {deny: [{workspace: 'Production*'}]}\n")
    with pytest.raises(DesiredStateError, match="workspace"):
        load_desired(path, registry)


def test_policy_merges_across_files(tmp_path, registry):
    path = _tree(tmp_path, "policy: {max_blast_radius: 3}\n", late="policy: {prune: true}\n")
    policy = load_desired(path, registry).policy
    assert policy.max_blast_radius == 3
    assert policy.prune is True


def test_versions_must_agree(tmp_path, registry):
    path = _tree(tmp_path, "version: 1\n", late="version: 2\n")
    with pytest.raises(DesiredStateError, match="disagree"):
        load_desired(path, registry)


def test_all_problems_are_reported_at_once(tmp_path, registry):
    path = _tree(
        tmp_path,
        """
        version: 7
        policy: {nonsense: true}
        mystery: 1
        workspaces:
          - name: a
            roles: [{principal: not-a-uuid, role: Admin}]
        """,
    )
    with pytest.raises(DesiredStateError) as exc:
        load_desired(path, registry)
    problems = exc.value.problems
    assert len(problems) >= 4, problems


def test_workspace_validation_errors_carry_a_path(tmp_path, registry):
    path = _tree(tmp_path, "workspaces: [{name: a, roles: [{principal: nope, role: Admin}]}]\n")
    with pytest.raises(DesiredStateError, match=r"workspaces\.0\.roles\.0\.principal"):
        load_desired(path, registry)


def test_missing_file(tmp_path, registry):
    with pytest.raises(DesiredStateError, match="not found"):
        load_desired(tmp_path / "nope.yaml", registry)


def test_empty_file_is_an_empty_desired_state(tmp_path, registry):
    path = _tree(tmp_path, "")
    state = load_desired(path, registry)
    assert state.sections == {}
    assert state.version == 1


def test_generic_merge():
    assert deep_merge({"a": {"x": 1}, "l": [1]}, {"a": {"y": 2}, "l": [2], "s": 3}) == {
        "a": {"x": 1, "y": 2},
        "l": [1, 2],
        "s": 3,
    }
