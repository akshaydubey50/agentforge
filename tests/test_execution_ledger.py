"""Phase 3's durable half: the ledger, dedupe, the crash window, and how a
resume routes what a dead worker left behind.

Not tier 1: this drives the real seams (execution.execute_tool,
graph/nodes._run_tool_call, escalations.apply_escalation_decision,
recovery.reconcile_orphaned_subtasks) against real Postgres and Redis, like
tests/test_policy_approval_flow.py. The pure half is
tests/test_execution_safety.py.

No LLM calls. Failure and crash injection use test-only tools declared here --
no production tool is modified to make it fail.

The properties, in the order they matter:

  an effect that already succeeded          -> is NOT executed again
  a read                                    -> IS executed again (deduping it
                                               would break the picker resume)
  a call in progress                        -> is on disk BEFORE the effect
  a crash mid-call, idempotent tool         -> cleared, safe to repeat
  a crash mid-call, unsafe tool             -> held, and a repeat is refused
  an approval resumed after the effect      -> does not repeat it
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sys
from threading import Event
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from sqlmodel import select

from agentsys import execution, policy, recovery
from agentsys.config import settings
from agentsys.db.models import Escalation, Subtask, SubtaskStatus, Task, ToolCall
from agentsys.db.session import get_session, init_db
from agentsys.escalations import apply_escalation_decision
from agentsys.execution import ExecutionSafety, execute_tool
from agentsys.graph import nodes
from agentsys.graph.schemas import ToolChoice
from agentsys.policy import ActionType, Risk
from agentsys.tools import registry as registry_module
from agentsys.tools.base import Tool, ToolResult
from conftest import get_test_owner_id


def setup_module() -> None:
    init_db()


# --- test-only tools -------------------------------------------------------


class EffectTool(Tool):
    """Stands in for a tool with a real effect. Records every run() so a test
    can prove not that a result came back, but that nothing executed."""

    name = "effect_tool"
    description = "Test double."
    action_type = ActionType.LOCAL_WRITE
    risk = Risk.MEDIUM

    def __init__(self, safety: ExecutionSafety = ExecutionSafety.IDEMPOTENT) -> None:
        self.execution_safety = safety
        self.calls: list[dict] = []

    def validate_args(self, proposed: dict) -> dict:
        return dict(proposed)

    def run(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        return ToolResult(success=True, output={"wrote": kwargs.get("path", "x"), "n": len(self.calls)})


class KilledTool(EffectTool):
    """A worker being SIGKILLed mid-call, as closely as a test can stage it:
    BaseException, which nothing in the execution path catches, so the process
    unwinds exactly where a kill would leave it -- after the effect started and
    before any outcome was recorded."""

    def run(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        raise SystemExit("simulated SIGKILL mid-call")


class BlockingEffectTool(EffectTool):
    """Lets a test hold the first worker inside run() while a second worker
    tries to claim the same effect."""

    def __init__(self) -> None:
        super().__init__(ExecutionSafety.IDEMPOTENT)
        self.started = Event()
        self.release = Event()

    def run(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        self.started.set()
        assert self.release.wait(5), "test timed out waiting to release the tool call"
        return ToolResult(success=True, output={"wrote": kwargs.get("path", "x"), "n": len(self.calls)})


class FlakyReadTool(Tool):
    name = "flaky_read"
    description = "Test double."
    action_type = ActionType.READ
    risk = Risk.LOW
    execution_safety = ExecutionSafety.IDEMPOTENT

    def __init__(self, failures: int = 1) -> None:
        self.failures = failures
        self.calls: list[dict] = []

    def validate_args(self, proposed: dict) -> dict:
        return dict(proposed)

    def run(self, **kwargs) -> ToolResult:
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            return ToolResult(success=False, error="connection reset by peer")
        return ToolResult(success=True, output={"answer": 42})


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch):
    monkeypatch.setattr(settings, "tool_backoff_base_seconds", 0.0)
    monkeypatch.setattr(settings, "tool_backoff_max_seconds", 0.0)


@pytest.fixture
def registered(monkeypatch):
    """Put a test tool in front of both registry bindings, for the same reason
    tests/test_policy_approval_flow.py's `spy` fixture does: nodes imports
    get_registry at module scope, delegate_subagent resolves it at call time."""

    def _install(tool: Tool) -> Tool:
        real = registry_module.get_registry()

        class Shim:
            def get(self, name):
                return tool if name == tool.name else real.get(name)

            def names(self):
                return [*real.names(), tool.name]

            def describe(self, exclude=frozenset()):
                return real.describe(exclude)

        monkeypatch.setattr(nodes, "get_registry", lambda: Shim())
        # registry_module covers execution.resolve_ambiguous_calls too: it
        # imports get_registry inside the function, so it resolves the patched
        # module attribute at call time.
        monkeypatch.setattr(registry_module, "get_registry", lambda: Shim())
        return tool

    return _install


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    monkeypatch.setattr(nodes.cost, "record_llm_call", lambda *a, **k: None)


def _task_and_subtask() -> tuple[str, str]:
    with get_session() as session:
        task = Task(request_text="phase 3", owner_id=get_test_owner_id())
        session.add(task)
        session.commit()
        subtask = Subtask(
            task_id=task.id, position=0, description="do the thing", status=SubtaskStatus.READY
        )
        session.add(subtask)
        session.commit()
        return task.id, subtask.id


def _rows(subtask_id: str) -> list[ToolCall]:
    with get_session() as session:
        return list(
            session.exec(
                select(ToolCall).where(ToolCall.subtask_id == subtask_id).order_by(ToolCall.created_at)
            ).all()
        )


# --- dedupe ----------------------------------------------------------------


def test_an_effect_that_already_succeeded_is_not_executed_again():
    tool = EffectTool()
    task_id, subtask_id = _task_and_subtask()
    args = {"path": "report.txt", "content": "hello"}

    first = execute_tool(
        tool, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE
    )
    second = execute_tool(
        tool, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE
    )

    assert len(tool.calls) == 1, "the second logical execution must not reach the tool"
    assert second.deduped is True
    assert second.result.success is True
    assert second.result.output == first.result.output, "the reused result must be the original one"


def test_concurrent_workers_do_not_execute_the_same_effect_twice():
    """The claim is atomic: a second worker racing the same effect sees the
    in-flight row and does not enter tool.run()."""
    tool = BlockingEffectTool()
    task_id, subtask_id = _task_and_subtask()
    args = {"path": "report.txt", "content": "hello"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            execute_tool,
            tool,
            args,
            task_id=task_id,
            subtask_id=subtask_id,
            action_type=ActionType.LOCAL_WRITE,
        )
        assert tool.started.wait(5), "first worker never reached tool.run()"

        second = pool.submit(
            execute_tool,
            tool,
            args,
            task_id=task_id,
            subtask_id=subtask_id,
            action_type=ActionType.LOCAL_WRITE,
        )
        second_outcome = second.result(timeout=5)
        assert second_outcome.refused is True
        assert len(tool.calls) == 1, "the racing worker must not reach the tool"

        tool.release.set()
        first_outcome = first.result(timeout=5)

    assert first_outcome.result.success is True
    assert len(tool.calls) == 1

    third = execute_tool(
        tool, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE
    )
    assert third.deduped is True
    assert len(tool.calls) == 1, "after success, later repeats reuse the ledger result"


def test_a_different_effect_is_not_deduped():
    tool = EffectTool()
    task_id, subtask_id = _task_and_subtask()

    execute_tool(tool, {"path": "a.txt"}, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE)
    execute_tool(tool, {"path": "b.txt"}, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE)

    assert len(tool.calls) == 2


def test_a_different_user_is_not_deduped():
    """Whose account an effect lands on is part of its identity. One user's
    completed action must never suppress another's."""
    tool = EffectTool()
    task_id, subtask_id = _task_and_subtask()
    args = {"path": "shared.txt"}

    execute_tool(tool, args, task_id=task_id, subtask_id=subtask_id,
                 action_type=ActionType.LOCAL_WRITE, user_id="alice")
    execute_tool(tool, args, task_id=task_id, subtask_id=subtask_id,
                 action_type=ActionType.LOCAL_WRITE, user_id="bob")

    assert len(tool.calls) == 2


def test_a_read_is_never_deduped():
    """Deduping reads would break a working flow, not just waste an index:
    google_photos_pick returns _awaiting_human, a human picks, and the resume
    calls the IDENTICAL tool with the IDENTICAL arguments -- that second call
    is the one that collects the selection. Re-searching after a write and
    polling a status are the same shape."""
    tool = FlakyReadTool(failures=0)
    task_id, subtask_id = _task_and_subtask()

    execute_tool(tool, {"q": "x"}, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.READ)
    execute_tool(tool, {"q": "x"}, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.READ)

    assert len(tool.calls) == 2
    assert all(row.effect_key is None for row in _rows(subtask_id)), "reads carry no effect key"


def test_a_failed_effect_does_not_block_a_later_attempt():
    """Dedupe is on SUCCESS. A failure is not an effect that happened."""

    class FailingTool(EffectTool):
        def run(self, **kwargs) -> ToolResult:
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return ToolResult(success=False, error="file not found: r.txt")
            return ToolResult(success=True, output={"ok": True})

    tool = FailingTool()
    task_id, subtask_id = _task_and_subtask()
    args = {"path": "r.txt"}

    execute_tool(tool, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE)
    second = execute_tool(tool, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE)

    assert len(tool.calls) == 2
    assert second.result.success is True


# --- the crash window ------------------------------------------------------


def test_the_attempt_is_on_disk_before_the_effect():
    """The whole ordering change. Before Phase 3 the single INSERT happened
    after the call returned, so a kill during the call left NOTHING -- and a
    resume could not tell 'never ran' from 'may have run'."""
    tool = KilledTool()
    task_id, subtask_id = _task_and_subtask()

    with pytest.raises(SystemExit):
        execute_tool(
            tool, {"path": "r.txt"}, task_id=task_id, subtask_id=subtask_id,
            action_type=ActionType.LOCAL_WRITE,
        )

    rows = _rows(subtask_id)
    assert len(rows) == 1, "the attempt must survive the kill"
    assert rows[0].success is None, "and it must say AMBIGUOUS, not failed and not succeeded"
    assert rows[0].effect_key, "with the identity a resume needs to recognise it"


def test_recovery_clears_an_ambiguous_call_for_a_tool_that_is_safe_to_repeat(registered):
    tool = registered(EffectTool(ExecutionSafety.IDEMPOTENT))
    task_id, subtask_id = _task_and_subtask()
    key = execution.effect_key(tool.name, {"path": "r.txt"}, task_id=task_id)
    execution._open_call(subtask_id, tool.name, {"path": "r.txt"}, key)

    counts = execution.resolve_ambiguous_calls([subtask_id])

    assert counts == {"resolved": 1, "held": 0}
    assert _rows(subtask_id)[0].success is False, "resolved to failed, so a repeat is allowed"


def test_recovery_holds_an_ambiguous_call_for_a_tool_that_is_not(registered):
    tool = registered(EffectTool(ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT))
    task_id, subtask_id = _task_and_subtask()
    key = execution.effect_key(tool.name, {"path": "r.txt"}, task_id=task_id)
    execution._open_call(subtask_id, tool.name, {"path": "r.txt"}, key)

    counts = execution.resolve_ambiguous_calls([subtask_id])

    assert counts == {"resolved": 0, "held": 1}
    assert _rows(subtask_id)[0].success is None, "still ambiguous, because it still is"


def test_a_held_ambiguous_effect_is_refused_rather_than_replayed(registered):
    """The unresolved NULL row IS the block. No new escalation kind and no new
    table: a refusal is a failed step, which the existing unproductive-streak
    machinery already escalates on."""
    tool = registered(EffectTool(ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT))
    task_id, subtask_id = _task_and_subtask()
    args = {"path": "r.txt"}
    key = execution.effect_key(tool.name, args, task_id=task_id)
    execution._open_call(subtask_id, tool.name, args, key)
    execution.resolve_ambiguous_calls([subtask_id])

    outcome = execute_tool(
        tool, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE
    )

    assert tool.calls == [], "an effect that may already have happened must not be replayed"
    assert outcome.refused is True
    assert outcome.result.success is False
    assert "outcome is unknown" in outcome.result.error


def test_recovery_allows_an_idempotent_tool_to_repeat_an_ambiguous_effect(registered):
    tool = registered(EffectTool(ExecutionSafety.IDEMPOTENT))
    task_id, subtask_id = _task_and_subtask()
    args = {"path": "r.txt"}
    key = execution.effect_key(tool.name, args, task_id=task_id)
    execution._open_call(subtask_id, tool.name, args, key)
    execution.resolve_ambiguous_calls([subtask_id])

    outcome = execute_tool(
        tool, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE
    )

    assert len(tool.calls) == 1
    assert outcome.result.success is True


def test_reconcile_routes_the_tool_calls_a_dead_worker_left_behind(registered):
    """End to end through the real resume entry point: run_task calls
    reconcile_orphaned_subtasks first on every invocation, and that is now
    where in-flight tool calls get routed."""
    tool = registered(EffectTool(ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT))
    task_id, subtask_id = _task_and_subtask()
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        subtask.status = SubtaskStatus.RUNNING  # what a killed worker leaves
        session.add(subtask)
        session.commit()
    key = execution.effect_key(tool.name, {"path": "r.txt"}, task_id=task_id)
    execution._open_call(subtask_id, tool.name, {"path": "r.txt"}, key)

    reconciled = recovery.reconcile_orphaned_subtasks(task_id)

    assert reconciled == 1
    with get_session() as session:
        assert session.get(Subtask, subtask_id).status is SubtaskStatus.FAILED
    assert _rows(subtask_id)[0].success is None, "held: this tool may not be replayed blindly"


def test_code_execution_is_treated_conservatively():
    """Its container is ephemeral, network-disabled and unmounted, so the
    durable effect is nil -- but arbitrary code a human approved once must not
    be replayed by a recovery nobody watched."""
    tool = registry_module.get_registry().get("code_execution")
    assert execution.safety_of(tool) is ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT

    task_id, subtask_id = _task_and_subtask()
    key = execution.effect_key("code_execution", {"code": "print(1)"}, task_id=task_id)
    execution._open_call(subtask_id, "code_execution", {"code": "print(1)"}, key)

    assert execution.resolve_ambiguous_calls([subtask_id]) == {"resolved": 0, "held": 1}


# --- retry through the real seam -------------------------------------------


def test_a_transient_failure_retries_and_succeeds_through_the_graph_seam(registered):
    tool = registered(FlakyReadTool(failures=1))
    task_id, subtask_id = _task_and_subtask()

    result = nodes._execute_subtask(
        task_id, subtask_id,
        preselected_choice=ToolChoice(tool_name=tool.name, tool_input_json='{"q": "x"}', rationale="t"),
    )

    assert result is True
    assert len(tool.calls) == 2
    rows = _rows(subtask_id)
    assert len(rows) == 1, "one call, retried -- not two calls"
    assert rows[0].success is True


def test_the_span_records_what_phase_3_did(registered):
    """Enough to diagnose a duplicate-effect or retry incident from the trace
    alone, and no arguments or output beyond what the span already carries."""
    from agentsys.db.models import TraceSpan

    tool = registered(FlakyReadTool(failures=1))
    task_id, subtask_id = _task_and_subtask()
    nodes._execute_subtask(
        task_id, subtask_id,
        preselected_choice=ToolChoice(tool_name=tool.name, tool_input_json='{"q": "x"}', rationale="t"),
    )

    with get_session() as session:
        span = session.exec(
            select(TraceSpan).where(TraceSpan.subtask_id == subtask_id, TraceSpan.span_type == "tool_call")
        ).first()
    meta = span.output["execution"]
    assert meta["attempts"] == 2
    assert meta["safety"] == "idempotent"
    assert meta["retry_reason"] == "transient", "a recovered retry must still say why"
    assert "failure_kind" not in meta, "it succeeded in the end"


# --- approval resume -------------------------------------------------------


def _gate(tool_name: str, task_id: str, subtask_id: str, args: str) -> str:
    nodes._execute_subtask(
        task_id, subtask_id,
        preselected_choice=ToolChoice(tool_name=tool_name, tool_input_json=args, rationale="t"),
    )
    with get_session() as session:
        return session.exec(
            select(Escalation).where(Escalation.task_id == task_id, Escalation.kind == "tool_approval")
        ).first().id


def test_an_approved_effect_executes_once_on_the_serialized_path(registered):
    tool = registered(EffectTool())
    task_id, subtask_id = _task_and_subtask()
    escalation_id = _gate(tool.name, task_id, subtask_id, '{"path": "r.txt"}')

    apply_escalation_decision(escalation_id, decision="approve", note="ok", decided_by="tester")

    assert len(tool.calls) == 1
    rows = _rows(subtask_id)
    assert len(rows) == 1 and rows[0].success is True and rows[0].effect_key


def test_resuming_an_approval_after_the_effect_already_succeeded_does_not_repeat_it(registered):
    """The approval path's own crash window: approve, worker dies mid-tool,
    the task resumes and cashes the approval again. Phase 2 re-checks that the
    call is still PERMITTED; Phase 3 is what checks whether it already RAN."""
    tool = registered(EffectTool())
    task_id, subtask_id = _task_and_subtask()
    escalation_id = _gate(tool.name, task_id, subtask_id, '{"path": "r.txt"}')

    # The pre-crash execution: the same logical effect, recorded as succeeded.
    with get_session() as session:
        context = session.get(Escalation, escalation_id).context
        session.add(
            ToolCall(
                subtask_id=subtask_id,
                tool_name=tool.name,
                input=context["kwargs"],
                output={"wrote": "r.txt", "n": 1},
                success=True,
                latency_ms=5,
                effect_key=execution.effect_key(
                    tool.name, context["kwargs"], task_id=task_id, user_id=get_test_owner_id()
                ),
            )
        )
        session.commit()

    apply_escalation_decision(escalation_id, decision="approve", note="ok", decided_by="tester")

    assert tool.calls == [], "the approved effect had already happened; it must not happen twice"
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
    assert subtask.status is SubtaskStatus.DONE, "reusing the prior result is a success, not a failure"
    assert "_reused" in subtask.output


# --- file_io, the first concrete effect ------------------------------------


def test_file_io_write_is_deduped_and_is_itself_idempotent():
    """Both halves, honestly. The write is naturally idempotent -- write_text
    truncates, so the same path and content give the same file however many
    times it runs. The ledger is what stops it running a second time at all."""
    file_io = registry_module.get_registry().get("file_io")
    assert execution.safety_of(file_io) is ExecutionSafety.IDEMPOTENT

    task_id, subtask_id = _task_and_subtask()
    args = {"action": "write", "path": "phase3.txt", "content": "written once", "task_id": task_id}

    first = execute_tool(
        file_io, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE
    )
    written = Path(first.result.output["path"])
    assert written.read_text(encoding="utf-8") == "written once"

    # Remove the file, then repeat the same logical action. If it executed
    # again the file would come back; deduped, it does not.
    written.unlink()
    second = execute_tool(
        file_io, args, task_id=task_id, subtask_id=subtask_id, action_type=ActionType.LOCAL_WRITE
    )

    assert second.deduped is True
    assert not written.exists(), "the second logical action must not have reached the filesystem"
    assert second.result.output == first.result.output


# --- the sub-agent seam ----------------------------------------------------


def test_a_sub_agent_gets_the_same_retry_safety(registered, monkeypatch):
    """§13: an ALLOWed call inside a sub-agent runs through the same execution
    safety. It gets no ledger row, and that is not a gap -- a sub-agent refuses
    everything policy does not ALLOW, and everything above a READ is
    REQUIRE_APPROVAL, so every call it can execute is a read."""
    from agentsys.graph.schemas import SubAgentStep
    from agentsys.tools import delegate_subagent as ds

    tool = registered(FlakyReadTool(failures=1))
    task_id, subtask_id = _task_and_subtask()
    steps = iter(
        [
            SubAgentStep(next_action="call_tool", tool_name=tool.name,
                         tool_input_json='{"q": "x"}', rationale="look"),
            SubAgentStep(next_action="finish", final_answer="done", rationale="have it"),
        ]
    )
    monkeypatch.setattr(ds, "structured_complete", lambda *a, **k: (next(steps), object()))
    monkeypatch.setattr(ds.cost, "record_llm_call", lambda *a, **k: None)

    result = ds.DelegateSubagentTool().run(goal="look", task_id=task_id, subtask_id=subtask_id)

    assert len(tool.calls) == 2, "the transient failure must have been retried"
    assert result.success is True
    assert "after 2 attempts" in result.output["steps"][0]
    assert _rows(subtask_id) == [], "a sub-agent read writes no ledger row"


# --- MCP -------------------------------------------------------------------


def test_an_undeclared_mcp_server_is_never_auto_retried():
    """Third-party code this repo has never seen. Already gated by policy;
    also never repeated after an ambiguous outcome."""
    from agentsys.tools.mcp_tool import MCPTool

    class FakeRemote:
        name = "do_something"
        description = "who knows"
        input_schema = {"type": "object"}

    tool = MCPTool("unknown_server", {"name": "unknown_server"}, FakeRemote())

    assert tool.execution_safety is ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT
    assert tool.action_type is ActionType.EXTERNAL_WRITE
    for kind in execution.FailureKind:
        assert not execution.is_retryable(kind, tool.execution_safety)


def test_an_operator_can_declare_a_trusted_mcp_server_safe():
    from agentsys.tools.mcp_tool import MCPTool

    class FakeRemote:
        name = "get_current_time"
        description = "the time"
        input_schema = {"type": "object"}

    config = {"name": "company_internal", "action_type": "read", "risk": "low",
              "execution_safety": "idempotent"}
    tool = MCPTool("company_internal", config, FakeRemote())

    assert tool.execution_safety is ExecutionSafety.IDEMPOTENT


def test_a_typo_in_an_mcp_declaration_falls_back_to_the_safe_default():
    from agentsys.tools.mcp_tool import MCPTool

    class FakeRemote:
        name = "x"
        description = "x"
        input_schema = {"type": "object"}

    tool = MCPTool("s", {"name": "s", "execution_safety": "totally-safe-honest"}, FakeRemote())

    assert tool.execution_safety is ExecutionSafety.NON_RETRYABLE_SIDE_EFFECT


# --- earlier phases still hold ---------------------------------------------


def test_policy_still_gates_an_effect_before_the_ledger_sees_it(registered):
    """Phase 3 sits AFTER Phase 2, not beside it: a call that needs approval
    must not reach execute_tool at all, so no ledger row exists for it."""
    tool = registered(EffectTool())
    task_id, subtask_id = _task_and_subtask()

    result = nodes._execute_subtask(
        task_id, subtask_id,
        preselected_choice=ToolChoice(tool_name=tool.name, tool_input_json='{"path": "r.txt"}', rationale="t"),
    )

    assert result is None, "gated"
    assert tool.calls == []
    assert _rows(subtask_id) == [], "nothing executed, so nothing in the ledger"


def test_validation_still_refuses_before_the_ledger_sees_it(registered):
    """Phase 1 likewise. A malformed proposal produces no ledger row, because
    a rejected proposal is not an effect."""
    file_io = registry_module.get_registry().get("file_io")
    task_id, subtask_id = _task_and_subtask()

    nodes._execute_subtask(
        task_id, subtask_id,
        preselected_choice=ToolChoice(tool_name="file_io", tool_input_json="not json", rationale="t"),
    )

    assert _rows(subtask_id) == []
    assert file_io is not None


def test_the_approval_fingerprint_still_covers_the_arguments():
    """Phase 2's binding is untouched by the new effect identity -- they are
    different hashes answering different questions, and both still exist."""
    args = {"path": "r.txt", "subtask_id": "s1"}
    assert policy.args_fingerprint("effect_tool", args) != policy.args_fingerprint(
        "effect_tool", {**args, "subtask_id": "s2"}
    ), "an approval is bound to one exact call in one exact step"
    assert execution.effect_key("effect_tool", args, task_id="T") == execution.effect_key(
        "effect_tool", {**args, "subtask_id": "s2"}, task_id="T"
    ), "an effect survives being re-proposed from a new subtask"
