from datetime import datetime, timezone

from agentsys import audit
from agentsys.db.models import Escalation, EscalationStatus, Subtask, SubtaskStatus, Task, TaskStatus
from agentsys.db.session import get_session
from agentsys.memory import short_term


class EscalationError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


_STATUS_MAP = {
    "approve": EscalationStatus.APPROVED,
    "reject": EscalationStatus.REJECTED,
    "take_over": EscalationStatus.TOOK_OVER,
}


def apply_escalation_decision(
    escalation_id: str,
    *,
    decision: str,
    note: str = "",
    decided_by: str = "human",
    override_output: str | None = None,
    actor_id: str | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[Escalation, bool]:
    """Applies a human decision to a pending escalation and updates the
    underlying task/subtask accordingly. Returns (escalation, should_resume) —
    should_resume tells the caller whether to enqueue run_agent_task again.
    Kept separate from the FastAPI route so it's directly callable from tests
    and from any future entry point (a CLI, a Slack action) without going
    through HTTP.

    That separation is also why the audit record is written HERE rather than
    in the route: this function is the one place every entry point has to
    pass through, so no future caller can approve an irreversible tool
    without leaving a record. actor_id/ip/user_agent are what the HTTP layer
    knows and this layer doesn't — optional, because a CLI or test genuinely
    has neither.
    """
    # Imported here, not at module scope: agentsys.graph.nodes pulls in the
    # whole tool registry, and this module is imported by main.py at startup.
    from agentsys.graph.nodes import (
        BUDGET_ESCALATION_KIND,
        UNPRODUCTIVE_FIELD,
        run_gated_tool_call,
        verify_gated_tool_call,
    )

    # Set only on the tool_approval/approve path, where a gated tool actually
    # runs as a direct consequence of the decision.
    gated_tool: tuple[str, bool] | None = None

    with get_session() as session:
        escalation = session.get(Escalation, escalation_id)
        if not escalation:
            raise EscalationError(404, "escalation not found")
        if escalation.status != EscalationStatus.PENDING:
            raise EscalationError(409, f"escalation already {escalation.status.value}")

        if decision == "take_over" and escalation.subtask_id is None:
            raise EscalationError(
                400, "take_over requires a subtask-level escalation; this one is plan-level"
            )
        if decision == "take_over" and not override_output:
            raise EscalationError(400, "take_over requires override_output")

        escalation.status = _STATUS_MAP[decision]
        escalation.decision_note = note
        escalation.decided_by = decided_by
        escalation.decided_at = datetime.now(timezone.utc)
        session.add(escalation)

        task = session.get(Task, escalation.task_id)

        if escalation.subtask_id:
            subtask = session.get(Subtask, escalation.subtask_id)
            if decision == "approve" and escalation.kind == "tool_approval":
                # Unlike the other three escalation kinds, nothing has
                # actually happened yet here -- _execute_subtask (graph/
                # nodes.py) held the tool call at the gate instead of
                # running it. "approve" means run it now, using the exact
                # tool_name/kwargs it was gated with (see that function's
                # _create_escalation call for how context got populated).
                tool_name = escalation.context.get("tool_name")
                if not tool_name:
                    raise EscalationError(500, "tool_approval escalation is missing tool_name in its context")
                # The whole approval snapshot goes through, not just the two
                # fields: run_gated_tool_call re-checks the recorded policy
                # decision and the argument fingerprint before executing, and
                # needs created_at to tell whether the approval is stale (see
                # settings.approval_expiry_seconds).
                tool_success, output_text = run_gated_tool_call(
                    escalation.task_id, escalation.subtask_id, escalation.context, escalation.created_at
                )
                if tool_success:
                    tool_success, verification_note = verify_gated_tool_call(
                        escalation.task_id, escalation.subtask_id, tool_success
                    )
                    if verification_note:
                        output_text = f"{output_text}\n{verification_note}"
                gated_tool = (tool_name, tool_success)
                subtask.output = output_text
                # A failure here (bad args, tool down) doesn't retry through
                # the reviewer -- it's reported the same way a rejected
                # subtask is, and the next agent_step decision sees the
                # failure in prior_context and can adapt.
                subtask.status = SubtaskStatus.DONE if tool_success else SubtaskStatus.FAILED
            elif decision == "approve":
                subtask.status = SubtaskStatus.DONE
            elif decision == "take_over":
                subtask.output = override_output
                subtask.status = SubtaskStatus.DONE
            else:  # reject
                subtask.status = SubtaskStatus.FAILED
            session.add(subtask)
            task.status = TaskStatus.RUNNING
        else:
            if decision == "reject":
                task.status = TaskStatus.FAILED
                task.final_output = f"Rejected by human ({decided_by}): {note}"
            else:  # approve — resume the existing plan as-is
                task.status = TaskStatus.RUNNING
                if escalation.kind == BUDGET_ESCALATION_KIND:
                    # The streak lives in Redis, not in a row, so unlike the
                    # step and cost budgets it can't be re-derived from
                    # escalation.decided_at -- it has to be cleared here.
                    # Without this, approving an unproductive-streak
                    # escalation resumes into a streak that is still at the
                    # limit and escalates again on the very next check.
                    short_term.set_value(escalation.task_id, UNPRODUCTIVE_FIELD, 0)

        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        session.refresh(escalation)

        should_resume = task.status == TaskStatus.RUNNING
        audit_fields = {
            "kind": escalation.kind,
            "task_id": escalation.task_id,
            "subtask_id": escalation.subtask_id,
            "status": escalation.status.value,
        }
        session.expunge(escalation)

    # Deliberately after the `with` block: the audit row must not claim a
    # decision that a rollback threw away. An audit log that runs slightly
    # behind reality is fine; one that records things which never happened
    # is worse than none.
    audit.record(
        audit.Action.ESCALATION_DECIDED,
        actor_id=actor_id,
        actor_label=decided_by,
        target_type="escalation",
        target_id=escalation_id,
        outcome="success" if decision != "reject" else "denied",
        ip=ip,
        user_agent=user_agent,
        meta={
            **audit_fields,
            "decision": decision,
            "note": note,
            "override_output_provided": override_output is not None,
        },
    )
    if gated_tool is not None:
        # A separate row from the decision itself, because they are separate
        # facts: a human approved X, and then X ran and either worked or
        # didn't. Approving code_execution and having it fail is a materially
        # different event from approving it and having it succeed, and the
        # single most important thing this log will ever be asked about.
        audit.record(
            audit.Action.TOOL_APPROVAL_EXECUTED,
            actor_id=actor_id,
            actor_label=decided_by,
            target_type="escalation",
            target_id=escalation_id,
            outcome="success" if gated_tool[1] else "failure",
            ip=ip,
            user_agent=user_agent,
            meta={"tool_name": gated_tool[0], "task_id": audit_fields["task_id"]},
        )
    return escalation, should_resume
