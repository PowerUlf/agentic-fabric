"""Phase 1: the pure parts of the transport layer, tested without a tenant."""

import asyncio

import pytest

from afabric.cli import _as_rows, _describe
from afabric.kernel.toolbus import RestBackend, ToolBus, _fill, _mcp_payload
from afabric.kernel.tools import CATALOG, RestOp, ToolSpec, spec


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


class TestMcpPayload:
    """Checked against the live Core MCP input schemas on 2026-09-10."""

    def test_body_nested_under_details(self):
        payload = _mcp_payload(
            spec("update_workspace_role"),
            {"workspaceId": "w1", "roleAssignmentId": "r1", "role": "Viewer"},
        )
        assert payload == {
            "WorkspaceId": "w1",
            "RoleAssignmentId": "r1",
            "Details": {"role": "Viewer"},
        }

    def test_create_workspace_stays_flat(self):
        payload = _mcp_payload(spec("create_workspace"), {"displayName": "x", "description": ""})
        assert payload == {"displayName": "x", "description": ""}

    def test_no_body_means_no_details_member(self):
        payload = _mcp_payload(spec("delete_workspace"), {"workspaceId": "w1"})
        assert payload == {"WorkspaceId": "w1"}

    def test_reads_are_unchanged(self):
        assert _mcp_payload(spec("list_workspaces"), {}) == {}


class TestFallback:
    class _Backend:
        def __init__(self, name):
            self.name, self.calls = name, []

        async def call(self, tool, args):
            self.calls.append(tool.name)
            return self.name

    def _bus(self):
        mcp, rest = self._Backend("mcp"), self._Backend("rest")
        return ToolBus(mcp, settings=None, fallback=rest), mcp, rest

    def test_tool_without_mcp_counterpart_goes_over_rest(self):
        bus, mcp, rest = self._bus()
        assert (
            asyncio.run(bus.call("assign_to_capacity", workspaceId="w", capacityId="c")) == "rest"
        )
        assert mcp.calls == []

    def test_everything_else_stays_on_mcp(self):
        bus, mcp, rest = self._bus()
        assert asyncio.run(bus.call("list_workspaces")) == "mcp"
        assert rest.calls == []


class TestGetArguments:
    """Arguments the path does not consume are query parameters, not silence."""

    class _Client:
        def __init__(self):
            self.seen = {}

        async def get_all(self, path, *, collection="value", params=None):
            self.seen = {"path": path, "collection": collection, "params": params}
            return []

        async def get_one(self, path, *, params=None):
            self.seen = {"path": path, "params": params}
            return {}

    def _bus(self):
        client = self._Client()
        return ToolBus(RestBackend(client), settings=None), client

    def test_a_filter_reaches_the_api(self):
        bus, client = self._bus()
        asyncio.run(bus.call("list_items", workspaceId="w1", type="Notebook"))
        assert client.seen["path"] == "/workspaces/w1/items"
        assert client.seen["params"] == {"type": "Notebook"}

    def test_a_tool_without_extra_arguments_sends_none(self):
        bus, client = self._bus()
        asyncio.run(bus.call("get_workspace", workspaceId="w1"))
        assert client.seen == {"path": "/workspaces/w1", "params": {}}


class TestModuleTools:
    """Tools a module declared are served like the kernel's own."""

    MODULE_TOOL = ToolSpec(
        name="list_things",
        description="a module's own endpoint",
        rest=RestOp("GET", "/workspaces/{workspaceId}/things"),
        paged=True,
    )

    def _bus(self):
        backend = TestFallback._Backend("rest")
        return ToolBus(backend, settings=None, tools={"list_things": self.MODULE_TOOL}), backend

    def test_a_module_tool_resolves_and_is_served(self):
        bus, backend = self._bus()
        assert asyncio.run(bus.call("list_things", workspaceId="w")) == "rest"
        assert backend.calls == ["list_things"]

    def test_the_catalog_still_resolves(self):
        bus, _ = self._bus()
        assert bus.spec("list_workspaces").name == "list_workspaces"

    def test_the_bus_catalog_holds_both(self):
        bus, _ = self._bus()
        catalog = bus.catalog()
        assert catalog["list_things"] is self.MODULE_TOOL
        assert set(CATALOG) <= set(catalog)

    def test_an_unknown_tool_still_raises(self):
        bus, _ = self._bus()
        with pytest.raises(KeyError, match="no_such_tool"):
            bus.spec("no_such_tool")


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
