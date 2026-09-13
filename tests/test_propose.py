"""Phase 4b: an intent becomes a validated fabric.d/ fragment."""

import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock

from afabric.kernel import agent
from afabric.kernel.modules import discover

REGISTRY = discover()

GOOD = """\
workspaces:
  - name: Sales Dev
    description: Development workspace for the sales team
    folders:
      - Bronze
"""


class FakeBus:
    async def call(self, name, **args):
        return []


def _tools():
    return build()[0]


def build():
    tools, box = agent.build_propose_tools(FakeBus(), REGISTRY)
    return {t.name: t for t in tools}, box


def _result(text="done", *, is_error=False, subtype="success", turns=2):
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=turns,
        session_id="s",
        result=text,
        total_cost_usd=0.02,
    )


def _fake_query(*messages, on_prompt=None):
    seen = {}

    async def query(*, prompt, options):
        seen["prompt"], seen["options"] = prompt, options
        if on_prompt:
            await on_prompt()
        for message in messages:
            yield message

    return query, seen


def _propose(query):
    return asyncio.run(
        agent.propose(FakeBus(), REGISTRY, "a dev workspace", model="m", query=query)
    )


# --- the fragment tools -----------------------------------------------------------


def test_schema_comes_from_the_modules_themselves():
    schemas = agent.schemas(REGISTRY)
    assert set(schemas) >= {"policy", "workspaces"}
    assert "name" in str(schemas["workspaces"])


def test_validate_accepts_a_good_fragment():
    result = asyncio.run(_tools()["validate_fragment"].handler({"yaml": GOOD}))
    assert '"valid": true' in result["content"][0]["text"].lower()


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        ("workspaces:\n  - description: no name\n", "name"),
        ("lakehouses:\n  - name: nope\n", "unknown top-level key"),
        ("workspaces: [", "not valid YAML"),
    ],
)
def test_validate_reports_problems(fragment, expected):
    result = asyncio.run(_tools()["validate_fragment"].handler({"yaml": fragment}))
    text = result["content"][0]["text"]
    assert '"valid": false' in text.lower() and expected in text


def test_submit_stores_the_fragment():
    tools, box = build()
    result = asyncio.run(
        tools["submit_proposal"].handler({"filename": "20-sales.yaml", "yaml": GOOD})
    )
    assert not result.get("is_error")
    assert box == {"filename": "20-sales.yaml", "yaml": GOOD}


def test_submit_refuses_an_invalid_fragment():
    tools, box = build()
    result = asyncio.run(
        tools["submit_proposal"].handler({"filename": "20-sales.yaml", "yaml": "workspaces: 3"})
    )
    assert result["is_error"] and not box


def test_submit_refuses_a_path():
    tools, box = build()
    result = asyncio.run(
        tools["submit_proposal"].handler({"filename": "../fabric.yaml", "yaml": GOOD})
    )
    assert result["is_error"] and not box


def test_no_write_tool_is_offered():
    names = set(_tools())
    assert {"declared_schema", "validate_fragment", "submit_proposal", "list_capacities"} <= names
    assert not names & {"create_workspace", "delete_workspace", "read_journal"}


# --- the loop ---------------------------------------------------------------------


def test_returns_the_submitted_fragment():
    built = {}

    def build(bus, registry):
        tools, box = agent.build_propose_tools(bus, registry)
        built["tools"] = {t.name: t for t in tools}
        built["box"] = box
        return tools, box

    async def submit():
        # Stands in for Claude calling the tool: the real handler, the real box.
        await built["tools"]["submit_proposal"].handler(
            {"filename": "20-sales.yaml", "yaml": GOOD}
        )

    use = ToolUseBlock(id="t1", name="mcp__afab__submit_proposal", input={})
    query, _ = _fake_query(
        AssistantMessage(content=[use], model="m"),
        _result("Declared one workspace. Fill in the capacity.", turns=4),
        on_prompt=submit,
    )
    proposal = asyncio.run(
        agent.propose(
            FakeBus(), REGISTRY, "a dev workspace", model="m", query=query, build=build
        )
    )

    assert proposal.filename == "20-sales.yaml"
    assert proposal.yaml == GOOD
    assert proposal.note.startswith("Declared one workspace")
    assert proposal.turns == 4 and proposal.tool_calls == ["submit_proposal"]


def test_a_run_without_a_submission_is_an_error():
    query, _ = _fake_query(_result("I could not do it"))
    with pytest.raises(agent.AgentError, match="without submitting"):
        _propose(query)


def test_system_prompt_is_the_proposing_one():
    query, seen = _fake_query(_result())
    with pytest.raises(agent.AgentError):
        _propose(query)
    assert "desired-state fragment" in seen["options"].system_prompt
