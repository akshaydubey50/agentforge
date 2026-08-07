"""MCP plugin-backbone tests -- real protocol, real subprocess, no mocks.

Every test here spawns the actual demo MCP server as a separate OS process and
talks to it over real stdio MCP, the same way a third party's server would be
reached. Mocking the client would prove only that the mock works; the entire
claim being tested is that the protocol boundary functions.
"""

import concurrent.futures

from agentsys.config import settings
from agentsys.tools.mcp_tool import MCPTool, discover_mcp_tools
from agentsys.tools.registry import _build_registry

DEMO_SERVER = settings.mcp_servers[0]


def test_discovery_finds_the_servers_advertised_tools():
    """AgentForge must learn a server's tools by asking it, not from local
    config -- that's what makes an unknown third-party server usable."""
    tools = discover_mcp_tools(DEMO_SERVER)
    names = {t.name for t in tools}

    assert names == {
        "mcp_company_internal_get_current_time",
        "mcp_company_internal_roll_dice",
        "mcp_company_internal_lookup_office",
    }
    for tool in tools:
        assert isinstance(tool, MCPTool)
        # The specialist LLM builds arguments from the description alone, so a
        # discovered tool is only usable if its schema made it into the prose.
        assert "Arguments:" in tool.description or "no arguments" in tool.description
        assert "company_internal" in tool.description


def test_calling_a_remote_tool_returns_real_computed_results():
    """Proves a real call crossed the process boundary rather than a cached or
    hardcoded value being echoed: dice are random, so repeated calls must vary
    while staying inside the requested bounds."""
    tool = next(t for t in discover_mcp_tools(DEMO_SERVER) if t.name.endswith("roll_dice"))

    seen = set()
    for _ in range(8):
        result = tool.run(sides=20, count=1)
        assert result.success, result.error
        rolls = result.output["rolls"]
        assert len(rolls) == 1
        assert 1 <= rolls[0] <= 20
        assert result.output["total"] == sum(rolls)
        seen.add(rolls[0])

    assert len(seen) > 1, "every roll identical — suspiciously not a live call"


def test_arguments_are_actually_passed_to_the_remote_tool():
    """A call that ignored its arguments would still 'succeed' — assert the
    remote side genuinely received them by using a bound only they could produce."""
    tool = next(t for t in discover_mcp_tools(DEMO_SERVER) if t.name.endswith("roll_dice"))

    result = tool.run(sides=2, count=25)
    assert result.success, result.error
    assert result.output["sides"] == 2
    assert len(result.output["rolls"]) == 25
    assert all(1 <= r <= 2 for r in result.output["rolls"])


def test_remote_tool_error_is_a_failed_result_not_an_exception():
    """A misbehaving external tool must surface as a rejectable ToolResult the
    reviewer can retry on, never as an exception that kills the graph run."""
    tool = next(t for t in discover_mcp_tools(DEMO_SERVER) if t.name.endswith("roll_dice"))

    result = tool.run(sides=1)  # server raises: sides must be at least 2

    assert result.success is False
    assert result.error


def test_unreachable_server_degrades_gracefully_in_the_registry():
    """A dead third-party server must cost only its own tools, never startup."""
    broken = {
        "name": "does_not_exist",
        "command": "definitely-not-a-real-executable-xyz",
        "args": [],
    }
    original = settings.mcp_servers
    settings.mcp_servers = [broken]
    try:
        registry = _build_registry()
    finally:
        settings.mcp_servers = original

    names = registry.names()
    assert not any(n.startswith("mcp_") for n in names)
    # The five first-party tools must be entirely unaffected.
    assert {"db_query", "file_io", "web_search", "code_execution", "delegate_subagent"} <= set(names)


def test_mcp_tools_are_registered_alongside_first_party_tools():
    """The 'just another Tool' design claim: nothing downstream should need to
    know a tool arrived over MCP."""
    registry = _build_registry()
    names = set(registry.names())

    assert "mcp_company_internal_roll_dice" in names
    assert {"db_query", "delegate_subagent"} <= names

    tool = registry.get("mcp_company_internal_get_current_time")
    result = tool.run()
    assert result.success, result.error
    assert "iso" in result.output


def test_concurrent_calls_from_multiple_threads_are_safe():
    """Parallel subtask waves execute tool calls on a thread pool, so a single
    MCPTool instance can be invoked from several threads at once -- the
    reconnect-per-call design exists specifically to make this safe."""
    tool = next(t for t in discover_mcp_tools(DEMO_SERVER) if t.name.endswith("roll_dice"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: tool.run(sides=6, count=1), range(4)))

    assert all(r.success for r in results), [r.error for r in results if not r.success]
    assert all(1 <= r.output["rolls"][0] <= 6 for r in results)


def test_server_only_data_is_reachable_and_unfakeable():
    """The actual value proposition: a tool whose data exists only on the
    external server, so it cannot be reproduced by code_execution, recalled by
    the model, or found on the web -- the situation a real third-party MCP
    server creates."""
    tool = next(t for t in discover_mcp_tools(DEMO_SERVER) if t.name.endswith("lookup_office"))

    result = tool.run(office_code="blr-2")  # lowercase: normalization happens server-side
    assert result.success, result.error
    assert result.output["city"] == "Bengaluru"
    assert result.output["desks"] == 412

    unknown = tool.run(office_code="ZZZ-9")
    assert unknown.success is False
    assert "unknown office code" in (unknown.error or "")


def test_descriptions_are_single_line():
    """A tool description must be one line. _tool_descriptions() renders the
    registry as one "- name: description" line per tool, so a description
    carrying the newlines and indentation of its source docstring breaks that
    list apart -- which was observed live to make the specialist skip an MCP
    tool that was the only possible source for the answer."""
    for tool in discover_mcp_tools(DEMO_SERVER):
        assert "\n" not in tool.description, f"{tool.name} description spans lines"
        assert "  " not in tool.description, f"{tool.name} description has collapsed-indent runs"
