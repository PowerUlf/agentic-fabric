"""Phase 1: the pure parts of the transport layer, tested without a tenant."""

import pytest

from afabric.cli import _as_rows, _describe
from afabric.kernel.toolbus import _fill
from afabric.kernel.tools import CATALOG, spec


class TestPathFilling:
    def test_substitutes_and_returns_leftovers(self):
        path, leftover = _fill(
            "/workspaces/{workspaceId}/items", {"workspaceId": "w1", "type": "Notebook"}
        )
        assert path == "/workspaces/w1/items"
        assert leftover == {"type": "Notebook"}

    def test_missing_argument_names_itself(self):
        with pytest.raises(KeyError, match="workspaceId"):
            _fill("/workspaces/{workspaceId}", {})

    def test_path_without_placeholders_passes_args_through(self):
        path, leftover = _fill("/workspaces", {"roleAssignments": True})
        assert path == "/workspaces"
        assert leftover == {"roleAssignments": True}


class TestCatalog:
    def test_every_tool_has_at_least_one_backend(self):
        for name, tool in CATALOG.items():
            assert tool.mcp_tool or tool.rest, f"{name} is unreachable on both transports"

    def test_names_match_their_key(self):
        for name, tool in CATALOG.items():
            assert tool.name == name

    def test_unknown_tool_lists_the_known_ones(self):
        with pytest.raises(KeyError, match="list_workspaces"):
            spec("no_such_tool")


class TestRowNormalization:
    """The two backends disagree on shape; the CLI must not care."""

    def test_bare_list(self):
        assert _as_rows([{"id": "1"}]) == [{"id": "1"}]

    def test_rest_envelope(self):
        assert _as_rows({"value": [{"id": "1"}]}) == [{"id": "1"}]

    def test_mcp_named_collection(self):
        assert _as_rows({"workspaces": [{"id": "1"}]}) == [{"id": "1"}]

    def test_single_object_becomes_one_row(self):
        assert _as_rows({"id": "1"}) == [{"id": "1"}]

    def test_empty_and_junk(self):
        assert _as_rows(None) == []
        assert _as_rows("some prose") == []
        assert _as_rows([{"id": "1"}, "junk"]) == [{"id": "1"}]


def _make_group(message, errors):
    """anyio raises ExceptionGroup on 3.11+; mimic the shape on 3.10."""
    try:
        return ExceptionGroup(message, errors)  # noqa: F821
    except NameError:
        class _Group(Exception):
            def __init__(self, msg, errs):
                super().__init__(msg)
                self.exceptions = errs

        return _Group(message, errors)


class TestErrorDescription:
    def test_walks_into_exception_groups(self):
        # ExceptionGroup is 3.11+; the package supports 3.10, so build it dynamically.
        group = _make_group("boom", [ValueError("the real cause")])
        lines = _describe(group)
        assert any("the real cause" in line for line in lines)

    def test_follows_cause_chain(self):
        try:
            try:
                raise ValueError("root")
            except ValueError as root:
                raise RuntimeError("wrapper") from root
        except RuntimeError as exc:
            lines = _describe(exc)
        assert any("root" in line for line in lines)
