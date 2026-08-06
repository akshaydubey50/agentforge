from datetime import datetime, timezone

from agentsys.db.models import Escalation, EscalationStatus, Subtask, SubtaskStatus, Task, TaskStatus
from agentsys.db.session import get_session


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
) -> tuple[Escalation, bool]:
    """Applies a human decision to a pending escalation and updates the
    underlying task/subtask accordingly. Returns (escalation, should_resume) —
    should_resume tells the caller whether to enqueue run_agent_task again.
    Kept separate from the FastAPI route so it's directly callable from tests
    and from any future entry point (a CLI, a Slack action) without going
    through HTTP.
    """
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
            if decision == "approve":
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

        task.updated_at = datetime.now(timezone.utc)
        session.add(task)
        session.commit()
        session.refresh(escalation)

        should_resume = task.status == TaskStatus.RUNNING
        return escalation, should_resume
