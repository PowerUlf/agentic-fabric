"""Phase 4: the agent explains drift through read-only tools."""

import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeSDKError, ResultMessage, ToolUseBlock

from afabric.kernel import agent
from afabric.kernel.journal import Journal
from afabric.kernel.tools import spec
from afabric.model.change import Change, Risk

CHANGE = Change(
    module="workspace",
    action="workspace.update",
    target="Sales Dev",
    risk=Risk.REVERSIBLE,
    before={"description": "old"},
    after={"description": "new"},
    metadata={"workspace_id": "ws-1"},
)


class FakeBus:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def call(self, name, **args):
        self.calls.append((name, args))
        if self.fail:
            raise RuntimeError("tenant said no")
        return [{"id": "f1", "displayName": "Bronze"}]


def _tools(bus=None, journal="missing.jsonl"):
    return {t.name: t for t in agent.build_tools(bus or FakeBus(), journal)}


def _result(text="ok", *, is_error=False, subtype="success", turns=2):
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=turns,
        session_id="s",
        result=text,
        total_cost_usd=0.01,
    )


def _fake_query(*messages, raises=None):
    seen = {}

    async def query(*, prompt, options):
        seen["prompt"], seen["options"] = prompt, options
        for message in messages:
            yield message
        if raises:
            raise raises

    return query, seen


def _explain(query):
    return asyncio.run(agent.explain(FakeBus(), [CHANGE], "j.jsonl", model="m", query=query))


# --- tool surface -----------------------------------------------------------------


def test_only_get_tools_are_offered():
    names = set(_tools())
    assert {"list_folders", "get_workspace", "read_journal"} <= names
    assert not names & {"create_workspace", "delete_workspace", "assign_to_capacity"}


def test_schema_requires_path_placeholders_and_offers_the_rest():
    schema = agent.tool_schema(spec("list_items"))
    assert schema["required"] == ["workspaceId"]
    # The type filter is optional and reaches the API as a query parameter.
    assert set(schema["properties"]) == {"workspaceId", "type"}
    assert agent.tool_schema(spec("list_workspaces"))["required"] == []


def test_tool_reads_through_the_bus():
    bus = FakeBus()
    result = asyncio.run(_tools(bus)["list_folders"].handler({"workspaceId": "ws-1"}))
    assert bus.calls == [("list_folders", {"workspaceId": "ws-1"})]
    assert "Bronze" in result["content"][0]["text"] and not result.get("is_error")


def test_tool_failure_is_reported_to_the_model():
    result = asyncio.run(_tools(FakeBus(fail=True))["get_workspace"].handler({"workspaceId": "x"}))
    assert result["is_error"] and "tenant said no" in result["content"][0]["text"]


def test_journal_tool_returns_newest_entries(tmp_path):
    journal = tmp_path / "j.jsonl"
    for run in ("r1", "r2", "r3"):
        Journal(journal, run_id=run).record("applied", CHANGE)
    result = asyncio.run(_tools(journal=journal)["read_journal"].handler({"limit": 2}))
    text = result["content"][0]["text"]
    assert '"r1"' not in text and '"r2"' in text and '"r3"' in text


# --- options ----------------------------------------------------------------------


def test_options_switch_off_everything_but_our_tools():
    query, seen = _fake_query(_result())
    _explain(query)
    options = seen["options"]

    assert options.tools == []
    assert options.permission_mode == "dontAsk"
    assert options.setting_sources == []
    assert options.env["ENABLE_TOOL_SEARCH"] == "false"
    assert set(options.mcp_servers) == {"afab"}
    assert options.allowed_tools and all(
        t.startswith("mcp__afab__") for t in options.allowed_tools
    )
    assert "mcp__afab__delete_workspace" not in options.allowed_tools


def test_system_prompt_carries_language_and_evidence_rule():
    query, seen = _fake_query(_result())
    asyncio.run(
        agent.explain(FakeBus(), [CHANGE], "j.jsonl", model="m", language="German", query=query)
    )
    system = seen["options"].system_prompt
    assert "in German" in system
    assert "not as confirmed" in system and "{" not in system


def test_plan_is_sent_without_module_metadata():
    query, seen = _fake_query(_result())
    _explain(query)
    assert "Sales Dev" in seen["prompt"] and "ws-1" not in seen["prompt"]
    assert "journal run" not in seen["prompt"]


def test_own_run_is_named_so_it_is_not_read_as_history():
    query, seen = _fake_query(_result())
    asyncio.run(
        agent.explain(FakeBus(), [CHANGE], "j.jsonl", model="m", run_id="abc123", query=query)
    )
    assert "journal run `abc123`" in seen["prompt"]


# --- loop -------------------------------------------------------------------------


def test_returns_result_and_tool_calls():
    use = ToolUseBlock(id="t1", name="mcp__afab__list_folders", input={"workspaceId": "ws-1"})
    query, _ = _fake_query(
        AssistantMessage(content=[use], model="m"),
        _result("The description was edited by hand.", turns=3),
    )

    result = _explain(query)

    assert result.text == "The description was edited by hand."
    assert result.turns == 3 and result.tool_calls == ["list_folders"]
    assert result.cost_usd == 0.01


def test_error_result_is_an_agent_error():
    query, _ = _fake_query(_result("", is_error=True, subtype="error_max_turns"))
    with pytest.raises(agent.AgentError, match="error_max_turns"):
        _explain(query)


def test_sdk_failure_is_an_agent_error():
    query, _ = _fake_query(raises=ClaudeSDKError("not logged in"))
    with pytest.raises(agent.AgentError, match="not logged in"):
        _explain(query)


def test_missing_result_is_an_agent_error():
    query, _ = _fake_query()
    with pytest.raises(agent.AgentError, match="without a result"):
        _explain(query)
