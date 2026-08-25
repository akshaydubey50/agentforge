"""The deterministic policy boundary: the LLM proposes, policy disposes.

Tier 1 -- pure functions, no API key, no network, no database, no Redis.
Everything here exercises policy.py directly plus the classification every
registered tool declares, so a broken rule is caught for free rather than by
spending an LLM call in the battery.

The DB-backed half of Phase 2 -- that REQUIRE_APPROVAL really does route into
the existing escalation flow, that a DENY never reaches run(), that an
approved call resumes as the exact call that was approved, and that a mutated
or expired approval is refused -- lives in tests/test_policy_approval_flow.py,
which needs real Postgres like the rest of this suite.

The shape being defended, from the architecture audit's 5.2: approval used to
be a boolean on a Python class that two of fifteen tools set. It could not
express risk, could not depend on arguments, and could not be reviewed
anywhere except by reading fifteen tool files.
"""

from __future__ import annotations

import pytest

from agentsys.policy import (
    ActionType,
    PolicyDecision,
    PolicyDecisionType,
    Risk,
    args_fingerprint,
    decide,
    snapshot,
)
from agentsys.tools.base import Tool, ToolResult, ToolValidationError, validated_kwargs
from agentsys.tools.code_execution import CodeExecutionTool
from agentsys.tools.db_query import DbQueryTool
from agentsys.tools.delegate_subagent import DelegateSubagentTool
from agentsys.tools.file_io import FileIOTool
from agentsys.tools.gmail import GmailReadTool, GmailSearchTool
from agentsys.tools.google_drive import GoogleDriveReadTool, GoogleDriveTool
from agentsys.tools.google_photos import GooglePhotosPickTool
from agentsys.tools.knowledge_search import KnowledgeSearchTool
from agentsys.tools.tweet_workshop import TweetWorkshopTool
from agentsys.tools.web_search import WebSearchTool

# Instantiated directly, not through get_registry() -- the registry probes
# Google OAuth config and spawns real MCP server subprocesses, neither of which
# belongs in a tier-1 test. Same reasoning as test_tool_contracts.py.
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


class _Fixture(Tool):
    """A tool that does not exist yet, so a rule for it can be tested before
    it does.

    There is no EXTERNAL_WRITE or DESTRUCTIVE tool in this repo (nothing sends
    email, posts content, or deletes anything), which is exactly why their
    rules need fixtures: the architecture has to support them before the tools
    land, or the first irreversible tool arrives with its policy written in the
    same hurry as its implementation.

    Named after the calls it stands in for -- send_email, delete_record -- so a
    failure reads as "the rule for send_email broke", not "the rule for
    FakeTool3 broke."
    """

    def __init__(self, name: str, action_type: ActionType, risk: Risk) -> None:
        self.name = name
        self.description = f"Fixture standing in for a real {name}."
        self.action_type = action_type
        self.risk = risk
        self.calls: list[dict] = []

    def run(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(success=True, output={"ran": True})


# --- the rule matrix -------------------------------------------------------


def test_low_risk_read_is_allowed():
    """web_search: the ordinary case, and the one that must not need a human."""
    result = decide(WebSearchTool(), {"query": "python release year", "max_results": 5})

    assert result.decision is PolicyDecisionType.ALLOW
    assert result.action_type is ActionType.READ
    assert result.risk is Risk.LOW


def test_medium_risk_read_is_allowed_because_its_guard_lives_elsewhere():
    """db_query is MEDIUM: arbitrary SQL on the app's own engine. Policy
    allowing it is deliberate -- the Phase-0 three-layer guard is the boundary,
    and gating every metrics lookup behind a human teaches humans to approve
    without reading. See the Phase-0 regression test at the bottom."""
    result = decide(DbQueryTool(), {"sql": "SELECT company FROM sample_metric"})

    assert result.decision is PolicyDecisionType.ALLOW
    assert result.risk is Risk.MEDIUM


def test_external_write_requires_approval():
    result = decide(
        _Fixture("send_email", ActionType.EXTERNAL_WRITE, Risk.MEDIUM),
        {"to": "someone@example.com", "subject": "hi"},
    )

    assert result.decision is PolicyDecisionType.REQUIRE_APPROVAL
    assert "outside this system" in result.reason


def test_destructive_action_is_denied_not_merely_gated():
    """DENY rather than REQUIRE_APPROVAL, and that choice is the point: there
    is no undo, no dry-run and no durable effect record yet (Phase 3), so
    there is no approval a human could give that would make it safe to resume
    after a crash. Asking would only move the blame."""
    result = decide(
        _Fixture("delete_record", ActionType.DESTRUCTIVE, Risk.HIGH),
        {"record_id": "abc"},
    )

    assert result.decision is PolicyDecisionType.DENY
    assert "no safe execution semantics" in result.reason


def test_critical_risk_is_denied():
    result = decide(_Fixture("wire_transfer", ActionType.EXTERNAL_WRITE, Risk.CRITICAL), {})

    assert result.decision is PolicyDecisionType.DENY
    assert "CRITICAL" in result.reason


def test_critical_beats_every_other_rule():
    """CRITICAL is checked first on purpose. A critical action must not become
    allowable by also being classified as a read."""
    for action_type in ActionType:
        result = decide(_Fixture("catastrophe", action_type, Risk.CRITICAL), {})
        assert result.decision is PolicyDecisionType.DENY, action_type


def test_high_risk_requires_approval():
    result = decide(CodeExecutionTool(), {"code": "print(1)", "timeout_s": 10})

    assert result.decision is PolicyDecisionType.REQUIRE_APPROVAL
    assert result.risk is Risk.HIGH


def test_an_uncovered_combination_is_gated_rather_than_allowed():
    """The catch-all at the end of the ruleset. A combination no rule names is
    a gap in the ruleset, and a gap must never read as permission."""
    result = decide(_Fixture("mystery", ActionType.READ, Risk.HIGH), {})

    assert result.decision is not PolicyDecisionType.ALLOW


# --- argument-dependent policy ---------------------------------------------


def test_same_tool_different_arguments_different_decision():
    """THE property Phase 2 exists to make possible: a decision is per-call,
    not per-tool. file_io is the real instance, not a manufactured one -- it
    shipped a needs_approval override for exactly this before policy existed.
    """
    tool = FileIOTool()

    read = decide(tool, {"action": "read", "path": "notes.txt", "content": None})
    write = decide(tool, {"action": "write", "path": "notes.txt", "content": "hello"})

    assert read.decision is PolicyDecisionType.ALLOW
    assert write.decision is PolicyDecisionType.REQUIRE_APPROVAL
    # And the effective classification moved with it, which is what the audit
    # record and the approval request both show a human.
    assert (read.action_type, read.risk) == (ActionType.READ, Risk.LOW)
    assert (write.action_type, write.risk) == (ActionType.LOCAL_WRITE, Risk.MEDIUM)


def test_listing_the_workspace_is_not_a_write():
    result = decide(FileIOTool(), {"action": "list", "path": ".", "content": None})

    assert result.decision is PolicyDecisionType.ALLOW


# --- policy sees validated arguments, never raw model JSON -----------------


def test_policy_is_never_reached_when_validation_rejects_the_proposal(monkeypatch):
    """The ordering contract both execution seams implement: validate, then
    decide, then run. A proposal that fails validation must not reach a rule
    that would go on to read a field it does not have.

    'write' vs 'WRITE' is the case that matters: the args model's Literal is
    case-sensitive, so the second is not a write with odd capitalisation -- it
    is not a valid call at all, and a policy rule doing args.get("action") ==
    "write" on the raw proposal would have quietly classified it as a read.
    """
    tool = FileIOTool()
    seen: list[dict] = []

    def _spy(_tool, args, **kwargs):
        seen.append(args)
        return PolicyDecision(
            decision=PolicyDecisionType.ALLOW, reason="spy", action_type=ActionType.READ, risk=Risk.LOW
        )

    # The miniature seam, in the same order as _execute_subtask and the
    # delegate_subagent loop.
    def seam(proposed_json: str) -> None:
        kwargs = validated_kwargs(tool, proposed_json, injected={"task_id": "t1"})
        _spy(tool, kwargs)

    with pytest.raises(ToolValidationError):
        seam('{"action": "WRITE", "path": "x", "content": "y"}')
    assert seen == [], "policy must not be consulted about a call that was refused"

    seam('{"action": "write", "path": "x", "content": "y"}')
    assert seen and seen[0]["action"] == "write"


def test_a_rule_reads_the_coerced_value_not_the_string_the_model_typed():
    """validated_kwargs coerces before policy sees anything, so a rule can
    compare against a real type. code_execution's timeout_s arrives as an int
    even when the model proposed the string "30"."""
    kwargs = validated_kwargs(CodeExecutionTool(), '{"code": "print(1)", "timeout_s": "30"}')

    assert kwargs["timeout_s"] == 30 and isinstance(kwargs["timeout_s"], int)
    assert decide(CodeExecutionTool(), kwargs).decision is PolicyDecisionType.REQUIRE_APPROVAL


# --- fail closed -----------------------------------------------------------


def test_a_raising_classification_denies_rather_than_allowing():
    """Security policy fails closed. An exception anywhere in the rule body
    must produce DENY -- a policy engine that can be made to fall open by
    breaking it is not a policy engine."""

    class Exploding(Tool):
        name = "exploding"
        description = "Its classification raises."

        @property
        def action_type(self):
            raise RuntimeError("classification blew up")

        def run(self, **kwargs) -> ToolResult:
            raise AssertionError("must never run")

    result = decide(Exploding(), {})

    assert result.decision is PolicyDecisionType.DENY
    assert "policy evaluation failed" in result.reason
    # The fallback must not read the tool back -- the tool is what raised.
    assert result.risk is Risk.CRITICAL


def test_the_fail_closed_path_survives_a_tool_with_no_attributes_at_all():
    class Nothing:
        pass

    result = decide(Nothing(), {})  # type: ignore[arg-type]

    assert result.decision is PolicyDecisionType.DENY


def test_decide_never_raises_whatever_it_is_handed():
    """Both call sites treat decide() as total. If it could raise, the
    delegate_subagent loop would crash mid-tool-call rather than refuse."""
    for junk in (None, object(), 42, "a string"):
        result = decide(junk, {})  # type: ignore[arg-type]
        assert result.decision is PolicyDecisionType.DENY


def test_an_unclassified_tool_is_gated_not_allowed():
    """Tool's defaults are the fail-closed pair. A tool whose author forgot to
    classify it must not be indistinguishable from one that is safe -- this is
    also what keeps a third-party MCP tool gated by default."""

    class Forgetful(Tool):
        name = "forgetful"
        description = "Declares no action_type and no risk."

        def run(self, **kwargs) -> ToolResult:
            return ToolResult(success=True)

    assert Tool.action_type is ActionType.EXTERNAL_WRITE
    assert Tool.risk is Risk.HIGH
    assert decide(Forgetful(), {}).decision is PolicyDecisionType.REQUIRE_APPROVAL


# --- every registered tool is classified, and classified plausibly ---------


@pytest.mark.parametrize("tool", LOCAL_TOOLS, ids=lambda t: t.name)
def test_every_local_tool_declares_a_real_classification(tool):
    """Not inherited from the fail-closed default by accident. A first-party
    tool silently sitting on the default would be gated in production and
    nobody would know why."""
    assert isinstance(tool.action_type, ActionType)
    assert isinstance(tool.risk, Risk)
    declared_explicitly = "action_type" in type(tool).__dict__ or "action_type" in vars(tool)
    assert declared_explicitly, f"{tool.name} inherits Tool's fail-closed default instead of declaring one"


@pytest.mark.parametrize("tool", LOCAL_TOOLS, ids=lambda t: t.name)
def test_no_local_tool_is_denied_outright(tool):
    """A registered tool that policy always denies is a tool the agent can
    see, propose, and never use -- which this repo's own rule (registry.py)
    says should not be advertised at all."""
    assert decide(tool, {}).decision is not PolicyDecisionType.DENY


def test_the_two_tools_that_were_gated_before_phase_2_are_still_gated():
    """The exact behaviour the deleted requires_approval/needs_approval pair
    provided, restated as a regression. These are the two cases the eval
    battery asserts on (file_write_requires_approval,
    code_execution_requires_approval), so a drift here fails a paid tier too.
    """
    assert decide(CodeExecutionTool(), {"code": "print(1)"}).decision is (
        PolicyDecisionType.REQUIRE_APPROVAL
    )
    assert decide(
        FileIOTool(), {"action": "write", "path": "notes.txt", "content": "hello"}
    ).decision is PolicyDecisionType.REQUIRE_APPROVAL


def test_read_only_tools_stay_ungated():
    """The other battery case, db_query_read_only_not_gated: a read must not
    start needing a human just because policy now exists."""
    for tool, args in [
        (DbQueryTool(), {"sql": "SELECT DISTINCT company FROM sample_metric"}),
        (WebSearchTool(), {"query": "x", "max_results": 5}),
        (KnowledgeSearchTool(), {"question": "What is the leave policy?"}),
        (GmailSearchTool(), {"query": "invoice"}),
        (TweetWorkshopTool(), {"brief": "a tweet about exports"}),
    ]:
        assert decide(tool, args).decision is PolicyDecisionType.ALLOW, tool.name


# --- approval binding ------------------------------------------------------


def test_the_snapshot_records_what_was_approved():
    kwargs = {"to": "a@example.com", "subject": "hi"}
    decision = decide(_Fixture("send_email", ActionType.EXTERNAL_WRITE, Risk.MEDIUM), kwargs)

    recorded = snapshot("send_email", kwargs, decision)

    assert recorded["tool_name"] == "send_email"
    assert recorded["kwargs"] == kwargs
    assert recorded["policy"]["decision"] == "require_approval"
    assert recorded["policy"]["reason"] == decision.reason
    assert recorded["args_fingerprint"]


def test_changing_a_protected_argument_changes_the_fingerprint():
    """send_email(to=A) approved must not resume as send_email(to=B). The
    resume path recomputes this and refuses on a mismatch -- see
    nodes._approval_refusal and the flow test that drives it end to end."""
    approved = args_fingerprint("send_email", {"to": "a@example.com", "subject": "hi"})
    mutated = args_fingerprint("send_email", {"to": "b@example.com", "subject": "hi"})

    assert approved != mutated


def test_the_fingerprint_covers_the_injected_plumbing_too():
    """Deliberately unlike deadcalls._signature, which strips these. user_id
    decides whose Gmail a call acts as and task_id decides which sandbox
    file_io writes to -- an approval that does not cover them covers nothing.
    """
    mine = args_fingerprint("gmail_search", {"query": "invoice", "user_id": "user-a"})
    theirs = args_fingerprint("gmail_search", {"query": "invoice", "user_id": "user-b"})

    assert mine != theirs


def test_the_fingerprint_ignores_key_order_only():
    same = args_fingerprint("file_io", {"action": "write", "path": "x"})
    reordered = args_fingerprint("file_io", {"path": "x", "action": "write"})
    different_tool = args_fingerprint("other_tool", {"action": "write", "path": "x"})

    assert same == reordered
    assert same != different_tool


def test_the_fingerprint_survives_an_unserialisable_argument():
    """default=str, so a value that isn't JSON-serialisable degrades to a
    stable string instead of raising inside the approval path."""
    weird = {"when": object()}

    assert args_fingerprint("t", weird) == args_fingerprint("t", weird)


# --- Phase 0 and Phase 1 regressions --------------------------------------


def test_policy_does_not_weaken_the_db_query_table_allowlist():
    """Phase 0. Policy ALLOWs db_query, which means the allowlist is still the
    entire boundary -- so it had better still be there. run() is not called
    here (no database in tier 1); the layer-1 prefilter is."""
    tool = DbQueryTool()

    assert decide(tool, {"sql": "SELECT * FROM app_user"}).decision is PolicyDecisionType.ALLOW
    # ... and the tool itself still refuses it, which is the point.
    assert tool._reject_reason("DELETE FROM app_user") is not None
    assert tool._reject_reason("SELECT 1; DROP TABLE task") is not None
    assert tool._reject_reason("SELECT * FROM sample_metric") is None


def test_typed_contract_validation_still_runs_before_policy():
    """Phase 1. The gate policy depends on is intact: a proposal naming a
    runtime-owned argument is refused outright (FileIOArgs forbids extras, so
    task_id is not merely overwritten -- nothing runs), and a malformed
    proposal still raises rather than degrading to an empty call.

    Both matter to Phase 2 specifically: policy is only meaningful if the
    kwargs it inspects are the ones the tool will actually receive."""
    with pytest.raises(ToolValidationError) as caught:
        validated_kwargs(
            FileIOTool(),
            '{"action": "write", "path": "x", "content": "y", "task_id": "attacker-chosen"}',
            injected={"task_id": "real-task"},
        )
    assert caught.value.field == "task_id"

    with pytest.raises(ToolValidationError):
        validated_kwargs(FileIOTool(), "not json at all")

    # And the legitimate call still comes through with the runtime's value.
    kwargs = validated_kwargs(
        FileIOTool(), '{"action": "write", "path": "x", "content": "y"}', injected={"task_id": "real-task"}
    )
    assert kwargs["task_id"] == "real-task"
    assert decide(FileIOTool(), kwargs).decision is PolicyDecisionType.REQUIRE_APPROVAL
