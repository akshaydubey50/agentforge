"""Policy routed through the EXISTING approval machinery, end to end.

Not tier 1: this drives the real graph seams (graph/nodes.py's
_execute_subtask, escalations.apply_escalation_decision) against real
Postgres and Redis, like tests/test_escalation_resume.py. The pure ruleset
lives in tests/test_policy.py.

No LLM calls: structured_complete is replaced by a fixture that returns a
chosen tool call, so what is under test is the boundary -- which decisions
run a tool, which park an escalation, which refuse -- and never the model.

The four properties, in the order they matter:

  ALLOW              -> the existing execution path, tool actually runs
  REQUIRE_APPROVAL   -> the existing tool_approval escalation, tool does NOT
                        run, and the exact proposed call is recorded
  DENY               -> refused, run() never reached, no escalation created
  approval, cashed   -> the exact approved call runs, and only if it is still
                        the approved call and still fresh
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from sqlmodel import select

from agentsys import policy
from agentsys.config import settings
from agentsys.db.models import (
    AuditEvent,
    Escalation,
    EscalationStatus,
    Subtask,
    SubtaskStatus,
    Task,
    ToolCall,
)
from agentsys.db.session import get_session, init_db
from agentsys.escalations import apply_escalation_decision
from agentsys.graph import nodes
from agentsys.graph.schemas import ToolChoice
from agentsys.policy import ActionType, PolicyDecisionType, Risk
from agentsys.tools import registry as registry_module
from agentsys.tools.base import Tool, ToolResult
from conftest import get_test_owner_id


def setup_module() -> None:
    init_db()


# --- a tool the registry will hand back, whose run() we can watch ----------


class SpyTool(Tool):
    """Records every run() it receives, so a test can prove not just that a
    decision was made but that nothing executed."""

    name = "spy_tool"
    description = "Test double."

    def __init__(self, action_type: ActionType, risk: Risk) -> None:
        self.action_type = action_type
        self.risk = risk
        self.calls: list[dict] = []

    def validate_args(self, proposed: dict) -> dict:
        # No args_model: this stands in for tools of every classification, so
        # it accepts whatever the test proposes. The validation contract itself
        # is Phase 1's and is tested in tests/test_policy.py.
        return dict(proposed)

    def run(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(success=True, output={"ran": True})


@pytest.fixture
def spy(monkeypatch):
    """Registers a SpyTool of a chosen classification and points the graph's
    registry lookups at it."""

    def _install(action_type: ActionType = ActionType.READ, risk: Risk = Risk.LOW) -> SpyTool:
        tool = SpyTool(action_type, risk)
        real = nodes.get_registry()

        class Shim:
            def get(self, name):
                return tool if name == tool.name else real.get(name)

            def names(self):
                return [*real.names(), tool.name]

            def describe(self, exclude=frozenset()):
                return real.describe(exclude)

        # BOTH bindings, because the two execution seams reach the registry
        # differently: graph/nodes.py imports get_registry at module scope, so
        # nodes.get_registry is its own name, while delegate_subagent imports
        # it inside run() (to avoid an import cycle) and therefore resolves
        # agentsys.tools.registry.get_registry at call time. Patching only the
        # first leaves the sub-agent looking at the real registry, which does
        # not contain spy_tool -- so the loop skips the step as "not available"
        # and a test meant to prove the gate fires proves nothing instead.
        monkeypatch.setattr(nodes, "get_registry", lambda: Shim())
        monkeypatch.setattr(registry_module, "get_registry", lambda: Shim())
        return tool

    return _install


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    monkeypatch.setattr(nodes.cost, "record_llm_call", lambda *a, **k: None)


def _make_task_and_subtask() -> tuple[str, str]:
    with get_session() as session:
        task = Task(request_text="policy flow", owner_id=get_test_owner_id())
        session.add(task)
        session.commit()
        subtask = Subtask(
            task_id=task.id, position=0, description="do the thing", status=SubtaskStatus.READY
        )
        session.add(subtask)
        session.commit()
        return task.id, subtask.id


def _choice(tool_name: str = "spy_tool", args: str = '{"x": 1}') -> ToolChoice:
    return ToolChoice(tool_name=tool_name, tool_input_json=args, rationale="test")


def _escalations(task_id: str, kind: str = "tool_approval") -> list[Escalation]:
    with get_session() as session:
        return list(
            session.exec(
                select(Escalation).where(Escalation.task_id == task_id, Escalation.kind == kind)
            ).all()
        )


def _tool_calls(subtask_id: str) -> list[ToolCall]:
    with get_session() as session:
        return list(session.exec(select(ToolCall).where(ToolCall.subtask_id == subtask_id)).all())


# --- ALLOW -----------------------------------------------------------------


def test_allow_runs_the_tool_on_the_existing_path(spy):
    tool = spy(ActionType.READ, Risk.LOW)
    task_id, subtask_id = _make_task_and_subtask()

    result = nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    assert result is True
    assert tool.calls == [{"x": 1}], "an allowed call must actually execute"
    assert _escalations(task_id) == [], "an allowed call must not ask for approval"
    # The ordinary recording is untouched -- a ToolCall row, as before policy.
    assert len(_tool_calls(subtask_id)) == 1


# --- REQUIRE_APPROVAL ------------------------------------------------------


def test_require_approval_routes_into_the_existing_escalation_flow(spy):
    tool = spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()

    result = nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    # The sentinel agent_step_node keys on: gated, so do not review this step.
    assert result is None
    assert tool.calls == [], "a gated call must not run before it is approved"

    escalations = _escalations(task_id)
    assert len(escalations) == 1
    escalation = escalations[0]
    assert escalation.kind == "tool_approval"
    assert escalation.status is EscalationStatus.PENDING
    assert escalation.subtask_id == subtask_id

    with get_session() as session:
        assert session.get(Subtask, subtask_id).status is SubtaskStatus.ESCALATED


def test_the_escalation_carries_the_full_approval_snapshot(spy):
    """What was approved has to be knowable later: the tool, the exact
    validated arguments, the decision that asked, and its reason."""
    spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()

    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice(args='{"to": "a@example.com"}'))

    context = _escalations(task_id)[0].context
    assert context["tool_name"] == "spy_tool"
    assert context["kwargs"] == {"to": "a@example.com"}
    assert context["policy"]["decision"] == "require_approval"
    assert context["policy"]["action_type"] == "external_write"
    assert context["policy"]["risk"] == "medium"
    assert context["policy"]["reason"]
    assert context["args_fingerprint"] == policy.args_fingerprint("spy_tool", {"to": "a@example.com"})


def test_approving_runs_the_exact_call_that_was_approved(spy):
    tool = spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice(args='{"to": "a@example.com"}'))
    escalation_id = _escalations(task_id)[0].id

    _, should_resume = apply_escalation_decision(
        escalation_id, decision="approve", note="ok", decided_by="tester"
    )

    assert should_resume is True
    assert tool.calls == [{"to": "a@example.com"}], "must run the approved call, unchanged"
    with get_session() as session:
        assert session.get(Subtask, subtask_id).status is SubtaskStatus.DONE
        assert session.get(Escalation, escalation_id).status is EscalationStatus.APPROVED


def test_rejecting_never_runs_the_tool(spy):
    tool = spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())
    escalation_id = _escalations(task_id)[0].id

    apply_escalation_decision(escalation_id, decision="reject", note="no", decided_by="tester")

    assert tool.calls == []
    with get_session() as session:
        assert session.get(Subtask, subtask_id).status is SubtaskStatus.FAILED


# --- DENY ------------------------------------------------------------------


def test_deny_never_calls_run_and_never_asks_a_human(spy):
    """A denied action is not a gated one. Asking a human to approve something
    the system has no safe way to perform would only move the blame."""
    tool = spy(ActionType.DESTRUCTIVE, Risk.HIGH)
    task_id, subtask_id = _make_task_and_subtask()

    result = nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    assert tool.calls == [], "a denied call must never reach run()"
    assert result is False, "denied is a failed step, not a gated one"
    assert _escalations(task_id) == [], "DENY must not create an approval request"
    assert _tool_calls(subtask_id) == [], "nothing executed, so nothing to record as executed"

    with get_session() as session:
        output = session.get(Subtask, subtask_id).output
    assert "refused by policy" in output


def test_critical_risk_is_denied_through_the_real_seam(spy):
    tool = spy(ActionType.READ, Risk.CRITICAL)
    task_id, subtask_id = _make_task_and_subtask()

    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    assert tool.calls == []
    assert _escalations(task_id) == []


def test_a_denied_step_counts_once_against_the_unproductive_streak(spy):
    """_rejected_call marks the step unproductive; the bookkeeping at the end
    of _execute_subtask must not mark it a second time, or one refusal would
    burn two of settings.max_unproductive_steps."""
    spy(ActionType.DESTRUCTIVE, Risk.HIGH)
    task_id, subtask_id = _make_task_and_subtask()
    nodes.short_term.set_value(task_id, nodes.UNPRODUCTIVE_FIELD, 0)

    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    assert nodes._unproductive_streak(task_id) == 1


# --- approval mutation ----------------------------------------------------


def test_changing_the_arguments_after_approval_refuses_the_call(spy):
    """approve send_email(to=A), resume with send_email(to=B) -> refused.

    Nothing in the API can do this today, which is the point: the check is
    what stops a future approver UI or a re-deriving resume path from
    substituting a different call under an existing approval.
    """
    tool = spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice(args='{"to": "a@example.com"}'))
    escalation_id = _escalations(task_id)[0].id

    # Mutate the stored call, leaving the fingerprint of what was approved.
    with get_session() as session:
        escalation = session.get(Escalation, escalation_id)
        escalation.context = {**escalation.context, "kwargs": {"to": "b@example.com"}}
        session.add(escalation)
        session.commit()

    apply_escalation_decision(escalation_id, decision="approve", note="ok", decided_by="tester")

    assert tool.calls == [], "a mutated call must not run under the old approval"
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        assert subtask.status is SubtaskStatus.FAILED
        assert "no longer match" in subtask.output


def test_a_reclassified_tool_cannot_be_unlocked_by_an_old_approval(spy):
    """The other half of re-checking at resume: policy is re-evaluated, so a
    tool that has become DENY between proposal and approval stays denied. No
    approval is sufficient for a denied action, including one already given.
    """
    tool = spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())
    escalation_id = _escalations(task_id)[0].id

    tool.action_type = ActionType.DESTRUCTIVE  # reclassified after the request

    apply_escalation_decision(escalation_id, decision="approve", note="ok", decided_by="tester")

    assert tool.calls == []
    with get_session() as session:
        assert "policy now denies" in session.get(Subtask, subtask_id).output


# --- approval expiry ------------------------------------------------------


def test_a_stale_approval_does_not_execute(spy):
    tool = spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())
    escalation_id = _escalations(task_id)[0].id

    with get_session() as session:
        escalation = session.get(Escalation, escalation_id)
        escalation.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=settings.approval_expiry_seconds + 60
        )
        session.add(escalation)
        session.commit()

    apply_escalation_decision(escalation_id, decision="approve", note="ok", decided_by="tester")

    assert tool.calls == [], "an expired approval must not run the tool"
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        assert subtask.status is SubtaskStatus.FAILED
        assert "Approval expired" in subtask.output


def test_a_fresh_approval_still_executes(spy):
    """The expiry must not be a blanket refusal -- the ordinary case still
    works, which is what makes this a window and not a wall."""
    tool = spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    apply_escalation_decision(
        _escalations(task_id)[0].id, decision="approve", note="ok", decided_by="tester"
    )

    assert len(tool.calls) == 1


def test_an_expired_approval_leaves_the_task_able_to_try_again(spy):
    """Refusing must not strand the task. The subtask fails with a readable
    reason and the task goes back to RUNNING, so the loop re-proposes and a
    fresh policy evaluation raises a fresh approval request."""
    spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())
    escalation_id = _escalations(task_id)[0].id

    with get_session() as session:
        escalation = session.get(Escalation, escalation_id)
        escalation.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=3)
        session.add(escalation)
        session.commit()

    _, should_resume = apply_escalation_decision(
        escalation_id, decision="approve", note="ok", decided_by="tester"
    )

    assert should_resume is True, "the task must be able to carry on and re-propose"


# --- the second execution seam --------------------------------------------


def test_a_sub_agent_cannot_bypass_the_gate_by_delegating(spy, monkeypatch):
    """The hole this phase found. _execute_subtask checked needs_approval; the
    delegate_subagent loop checked nothing, so a sub-agent proposing
    code_execution simply ran it. Both seams now evaluate policy.

    A sub-agent has no way to pause for a human, so its enforcement is refuse
    rather than escalate -- strictly safer than executing, and the parent agent
    can still propose the action directly and get a real approval."""
    from agentsys.graph.schemas import SubAgentStep
    from agentsys.tools import delegate_subagent as ds

    tool = spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()

    steps = iter(
        [
            SubAgentStep(
                next_action="call_tool", tool_name="spy_tool",
                tool_input_json='{"to": "a@b.c"}', rationale="try it",
            ),
            SubAgentStep(next_action="finish", final_answer="could not do it", rationale="blocked"),
        ]
    )
    monkeypatch.setattr(ds, "structured_complete", lambda *a, **k: (next(steps), object()))
    monkeypatch.setattr(ds.cost, "record_llm_call", lambda *a, **k: None)

    result = ds.DelegateSubagentTool().run(goal="send a mail", task_id=task_id, subtask_id=subtask_id)

    assert tool.calls == [], "a sub-agent must not execute a call that needs approval"
    assert result.success is True
    refusals = [line for line in result.output["steps"] if "refused" in line]
    assert len(refusals) == 1
    assert "cannot" in refusals[0]


def test_a_sub_agent_still_runs_allowed_calls(spy, monkeypatch):
    """The gate must not turn into a blanket refusal for sub-agents."""
    from agentsys.graph.schemas import SubAgentStep
    from agentsys.tools import delegate_subagent as ds

    tool = spy(ActionType.READ, Risk.LOW)
    task_id, subtask_id = _make_task_and_subtask()

    steps = iter(
        [
            SubAgentStep(
                next_action="call_tool", tool_name="spy_tool",
                tool_input_json='{"q": "x"}', rationale="look it up",
            ),
            SubAgentStep(next_action="finish", final_answer="done", rationale="have it"),
        ]
    )
    monkeypatch.setattr(ds, "structured_complete", lambda *a, **k: (next(steps), object()))
    monkeypatch.setattr(ds.cost, "record_llm_call", lambda *a, **k: None)

    ds.DelegateSubagentTool().run(goal="look it up", task_id=task_id, subtask_id=subtask_id)

    assert tool.calls == [{"q": "x"}]


# --- auditability ---------------------------------------------------------


def _audit_actions(task_id: str) -> list[str]:
    with get_session() as session:
        rows = session.exec(
            select(AuditEvent).where(AuditEvent.target_id == task_id).order_by(AuditEvent.seq)
        ).all()
    return [r.action for r in rows]


def test_a_gated_decision_lands_on_the_existing_audit_chain(spy):
    spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()

    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice(args='{"to": "a@b.c"}'))

    assert "policy.tool.decision" in _audit_actions(task_id)
    with get_session() as session:
        row = session.exec(
            select(AuditEvent)
            .where(AuditEvent.target_id == task_id, AuditEvent.action == "policy.tool.decision")
            .order_by(AuditEvent.seq.desc())
        ).first()

    assert row.meta["tool_name"] == "spy_tool"
    assert row.meta["action_type"] == "external_write"
    assert row.meta["risk"] == "medium"
    assert row.meta["decision"] == "require_approval"
    assert row.meta["reason"]
    # Argument NAMES, never argument values: the values are model-proposed text
    # shaped by untrusted content, they are already on the escalation and the
    # span, and audit.redact() is a key-name filter that would not catch a
    # secret pasted into a value.
    assert row.meta["argument_keys"] == ["to"]
    assert "a@b.c" not in str(row.meta)


def test_a_denied_decision_is_audited_too(spy):
    spy(ActionType.DESTRUCTIVE, Risk.HIGH)
    task_id, subtask_id = _make_task_and_subtask()

    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    with get_session() as session:
        row = session.exec(
            select(AuditEvent)
            .where(AuditEvent.target_id == task_id, AuditEvent.action == "policy.tool.decision")
            .order_by(AuditEvent.seq.desc())
        ).first()

    assert row.meta["decision"] == "deny"
    assert row.outcome == "denied"


def test_an_allowed_call_does_not_write_a_policy_audit_row(spy):
    """Deliberate: an ALLOW is the ordinary path and is already fully recorded
    as a tool_call span plus a ToolCall row. Writing a locked, hash-chained
    audit insert for every read would bury the decisions that matter."""
    spy(ActionType.READ, Risk.LOW)
    task_id, subtask_id = _make_task_and_subtask()

    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())

    assert "policy.tool.decision" not in _audit_actions(task_id)


def test_a_refused_approval_is_audited_as_its_own_event(spy):
    """"A human approved this and it still did not run" is exactly what an
    incident review asks about, so it must not look like a tool failure."""
    spy(ActionType.EXTERNAL_WRITE, Risk.MEDIUM)
    task_id, subtask_id = _make_task_and_subtask()
    nodes._execute_subtask(task_id, subtask_id, preselected_choice=_choice())
    escalation_id = _escalations(task_id)[0].id

    with get_session() as session:
        escalation = session.get(Escalation, escalation_id)
        escalation.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        session.add(escalation)
        session.commit()

    apply_escalation_decision(escalation_id, decision="approve", note="ok", decided_by="tester")

    assert "escalation.tool_approval.stale" in _audit_actions(task_id)
