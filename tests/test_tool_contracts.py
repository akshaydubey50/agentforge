"""Typed tool contracts: the LLM proposes, deterministic Python disposes.

Tier 1 -- pure functions, no API key, no network, no database. Everything here
exercises the one gate every proposed tool call goes through (tools/base.py's
validated_kwargs) plus the plumbing table both execution seams share
(graph/nodes.py's _injected_kwargs).

The shape being defended, from the architecture audit's §5.1: the model used to
emit free-text tool_input_json which was json.loads'd, fell back to {} on a
parse error, and went straight into run(**kwargs). A malformed proposal did not
fail -- it called the tool with no arguments at all and let every default stand
in for whatever the model meant.
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agentsys.graph import nodes
from agentsys.tools.base import Tool, ToolResult, ToolValidationError, ToolRegistry, validated_kwargs
from agentsys.tools.code_execution import CodeExecutionTool
from agentsys.tools.db_query import DbQueryTool
from agentsys.tools.delegate_subagent import DelegateSubagentTool
from agentsys.tools.file_io import FileIOTool
from agentsys.tools.gmail import GmailReadTool, GmailSearchTool
from agentsys.tools.google_drive import GoogleDriveReadTool, GoogleDriveTool
from agentsys.tools.google_photos import GooglePhotosPickTool
from agentsys.tools.knowledge_search import KnowledgeSearchTool
from agentsys.tools.mcp_tool import MCPTool
from agentsys.tools.tweet_workshop import TweetWorkshopTool
from agentsys.tools.web_search import WebSearchTool

# Every first-party executable tool, instantiated directly rather than through
# get_registry() -- the registry probes Google OAuth config and spawns real MCP
# server subprocesses, neither of which belongs in a tier-1 test.
LOCAL_TOOLS = [
    WebSearchTool(),
    FileIOTool(),
    DbQueryTool(),
    KnowledgeSearchTool(),
    DelegateSubagentTool(),
    TweetWorkshopTool(),
    GmailSearchTool(),
    GmailReadTool(),
    GoogleDriveTool(),
    GoogleDriveReadTool(),
    GooglePhotosPickTool(),
    CodeExecutionTool(),
]


class SpyTool(Tool):
    """Records every run() it receives, so a test can prove not just that a bad
    proposal raised, but that nothing executed."""

    name = "spy"
    description = "Test double."
    args_model = FileIOTool.args_model

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(success=True, output={"ran": True})


def attempt(tool: Tool, tool_input_json, **kw) -> ToolResult:
    """The execution seam in miniature, in the same order as _execute_subtask
    and the delegate_subagent loop: validate first, run only after."""
    return tool.run(**validated_kwargs(tool, tool_input_json, **kw))


@pytest.fixture
def spy():
    return SpyTool()


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    """_injected_kwargs reads the task's owner to decide whose Gmail/Drive a
    call acts as. The identity of that lookup is what's under test, not
    Postgres, so it's stubbed."""

    class _Session:
        def get(self, _model, _ident):
            return SimpleNamespace(owner_id="owner-from-the-task-row")

    @contextmanager
    def _get_session():
        yield _Session()

    monkeypatch.setattr(nodes, "get_session", _get_session)


# --- the four failure modes, kept distinguishable -------------------------


def test_valid_call_passes_exactly_the_proposed_arguments_through(spy):
    result = attempt(spy, '{"action": "write", "path": "report.txt", "content": "hi"}',
                     injected={"task_id": "task-1"})

    assert result.success
    assert spy.calls == [{"action": "write", "path": "report.txt", "content": "hi", "task_id": "task-1"}]


def test_missing_required_argument_is_rejected_before_the_tool_runs(spy):
    with pytest.raises(ToolValidationError) as caught:
        attempt(spy, '{"action": "read"}', injected={"task_id": "task-1"})

    assert caught.value.field == "path"
    assert "required" in str(caught.value).lower()
    assert spy.calls == []  # the whole point: nothing executed


def test_malformed_json_is_rejected_and_never_degrades_to_empty_arguments(spy):
    """The regression this phase exists for. `except JSONDecodeError: kwargs
    = {}` did not reject anything -- it ran the tool with no arguments."""
    with pytest.raises(ToolValidationError) as caught:
        attempt(spy, '{"action": "write", "path": ', injected={"task_id": "task-1"})

    assert "not valid JSON" in str(caught.value)
    assert spy.calls == []


def test_json_that_is_not_an_object_is_rejected(spy):
    with pytest.raises(ToolValidationError) as caught:
        attempt(spy, '["read", "notes.txt"]', injected={"task_id": "task-1"})

    assert "must be a JSON object" in str(caught.value)
    assert spy.calls == []


def test_unknown_tool_is_not_a_validation_failure():
    """Kept as the registry's own KeyError: the fix for an unknown tool is a
    different tool_name, not different arguments, and _execute_subtask reports
    the two differently."""
    registry = ToolRegistry()
    registry.register(WebSearchTool())

    with pytest.raises(KeyError):
        registry.get("send_email")


# --- types: coerced or rejected, but always per the declared schema --------


def test_a_string_that_is_really_an_integer_is_coerced_as_declared():
    kwargs = validated_kwargs(WebSearchTool(), '{"query": "acme q2", "max_results": "3"}')

    assert kwargs == {"query": "acme q2", "max_results": 3}


def test_a_value_of_the_wrong_type_is_rejected():
    with pytest.raises(ToolValidationError) as caught:
        validated_kwargs(WebSearchTool(), '{"query": "acme q2", "max_results": "several"}')

    assert caught.value.field == "max_results"


def test_a_value_outside_the_declared_range_is_rejected():
    """code_execution's timeout is a container wait held by a prefork worker
    child, so the bound is not decoration."""
    with pytest.raises(ToolValidationError) as caught:
        validated_kwargs(CodeExecutionTool(), '{"code": "print(1)", "timeout_s": 86400}')

    assert caught.value.field == "timeout_s"


def test_a_value_outside_the_declared_enum_is_rejected():
    with pytest.raises(ToolValidationError) as caught:
        validated_kwargs(FileIOTool(), '{"action": "delete", "path": "notes.txt"}')

    assert caught.value.field == "action"


# --- unknown arguments: fail closed, except where it was measured otherwise -


def test_action_capable_tools_forbid_unknown_arguments():
    with pytest.raises(ToolValidationError) as caught:
        validated_kwargs(FileIOTool(), '{"action": "read", "path": "a.txt", "encoding": "utf-8"}')

    assert caught.value.field == "encoding"


def test_generate_tweet_deliberately_allows_extras():
    """The one first-party exception, and it predates this phase: run() folds
    stray string kwargs into the brief because the model reliably produces
    them, and forbidding them here would re-break that. Not action-capable."""
    kwargs = validated_kwargs(TweetWorkshopTool(), '{"brief": "one-click export", "tone": "playful"}')

    assert kwargs == {"brief": "one-click export", "tone": "playful"}


# --- runtime-owned parameters ---------------------------------------------


@pytest.mark.parametrize(
    "tool, tool_name, proposal, owned",
    [
        (FileIOTool(), "file_io", '{"action": "read", "path": "a.txt", "task_id": "another-task"}', "task_id"),
        (GmailSearchTool(), "gmail_search", '{"query": "invoice", "user_id": "someone-else"}', "user_id"),
        (DelegateSubagentTool(), "delegate_subagent", '{"goal": "dig", "depth": 99}', "depth"),
    ],
)
def test_the_model_cannot_set_a_runtime_owned_parameter(tool, tool_name, proposal, owned):
    """Two of these are security boundaries rather than plumbing: task_id
    picks which task's sandbox file_io is confined to, user_id picks whose
    connected mailbox is read. Both used to be kwargs.setdefault(...), i.e.
    the proposed value won whenever the model supplied one."""
    injected = nodes._injected_kwargs(tool_name, "the-real-task", "the-real-subtask")

    with pytest.raises(ToolValidationError) as caught:
        validated_kwargs(tool, proposal, injected=injected)

    assert caught.value.field == owned  # forbidden outright, not silently dropped


def test_generate_tweet_extras_still_cannot_reach_the_plumbing():
    """extra="allow" means task_id arrives as an extra field rather than being
    rejected -- so the injected value has to win on ordering alone."""
    kwargs = validated_kwargs(
        TweetWorkshopTool(),
        '{"brief": "launch", "task_id": "another-task", "subtask_id": "another-subtask"}',
        injected=nodes._injected_kwargs("generate_tweet", "the-real-task", "the-real-subtask"),
    )

    assert kwargs["task_id"] == "the-real-task"
    assert kwargs["subtask_id"] == "the-real-subtask"


def test_a_runtime_default_may_still_be_overridden_by_the_model():
    """The other half of the ordering rule: delegate_subagent's goal falls back
    to the subtask description, but the model owns it and its value wins."""
    injected = nodes._injected_kwargs("delegate_subagent", "the-real-task", "the-real-subtask")

    fallback = validated_kwargs(DelegateSubagentTool(), "{}", defaults={"goal": "the subtask text"}, injected=injected)
    chosen = validated_kwargs(
        DelegateSubagentTool(), '{"goal": "something better"}', defaults={"goal": "the subtask text"}, injected=injected
    )

    assert fallback["goal"] == "the subtask text"
    assert chosen["goal"] == "something better"
    assert chosen["depth"] == 1


# --- every registered tool actually has a usable contract -----------------


@pytest.mark.parametrize("tool", LOCAL_TOOLS, ids=lambda t: t.name)
def test_every_local_tool_exposes_a_generated_schema(tool):
    schema = tool.args_schema()

    assert tool.args_model is not None
    assert schema["type"] == "object"
    # Maintainer commentary must not leak into what the model reads.
    assert "description" not in schema


@pytest.mark.parametrize("tool", LOCAL_TOOLS, ids=lambda t: t.name)
def test_every_local_tools_contract_plus_plumbing_equals_its_run_signature(tool):
    """The invariant that keeps the two halves honest. A parameter that is in
    neither the args model nor the injected set can never be supplied; an
    injected key that run() has no parameter for is a TypeError on every call
    -- which is exactly what shipped for gmail_* and google_drive_* when the
    Photos picker's task_id injection was applied to all five Google tools."""
    parameters = {
        name for name, p in inspect.signature(tool.run).parameters.items()
        if p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)
    }
    declared = set(tool.args_model.model_fields)
    injected = set(nodes._injected_kwargs(tool.name, "task-1", "subtask-1"))

    assert parameters == declared | injected


def test_the_catalogue_the_model_reads_carries_the_generated_schema():
    registry = ToolRegistry()
    registry.register(WebSearchTool())

    rendered = registry.describe()

    assert "- web_search: Searches the web" in rendered
    assert '"required": ["query"]' in rendered
    assert registry.describe(exclude=frozenset({"web_search"})) == "(no tools available)"


# --- MCP tools: same invariant, the server's own schema -------------------


def _mcp_tool(input_schema):
    """An MCPTool built from a stub of what discovery returns, so the argument
    contract can be tested without spawning a server. The real protocol path
    stays covered by tests/test_tool_mcp.py."""
    remote = SimpleNamespace(name="lookup_office", description="Looks up an office.", input_schema=input_schema)
    return MCPTool("company_internal", {"name": "company_internal"}, remote)


MCP_SCHEMA = {
    "type": "object",
    "properties": {"office_code": {"type": "string"}},
    "required": ["office_code"],
}


def test_mcp_arguments_are_validated_against_the_servers_advertised_schema():
    tool = _mcp_tool(MCP_SCHEMA)

    assert tool.validate_args({"office_code": "BLR-2"}) == {"office_code": "BLR-2"}
    with pytest.raises(ToolValidationError) as missing:
        validated_kwargs(tool, "{}")
    with pytest.raises(ToolValidationError) as wrong_type:
        validated_kwargs(tool, '{"office_code": 7}')

    assert "office_code" in str(missing.value)
    assert wrong_type.value.field == "office_code"


def test_mcp_malformed_json_is_rejected_like_any_other_tool():
    with pytest.raises(ToolValidationError):
        validated_kwargs(_mcp_tool(MCP_SCHEMA), "not json at all")


def test_an_mcp_server_advertising_no_schema_still_gets_a_call():
    """Documented limit, asserted so it stays deliberate: with nothing to
    validate against, inventing constraints would reject calls the server
    would have accepted. The server validates on its side."""
    assert _mcp_tool(None).validate_args({"anything": 1}) == {"anything": 1}
    assert _mcp_tool(None).args_schema() is None
