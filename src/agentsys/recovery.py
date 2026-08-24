"""Crash recovery for the agent worker.

Two complementary mechanisms, because a worker can die two different ways and
Postgres-as-source-of-truth only protects *committed* state, not the in-flight
node that was running when the process was killed:

1. reconcile_orphaned_subtasks(task_id) -- called at the very start of every
   graph invocation (see graph/runner.run_task). Execution within a task is
   strictly sequential and in-process, so when a *fresh* invocation begins,
   NO subtask for that task can legitimately still be in an in-flight state
   (RUNNING / NEEDS_REVISION). Any that are were left behind by a previous run
   that died mid-step -- they're zombies. We mark them FAILED so they stop
   polluting the step count and the UI, and so the loop cleanly authors the
   next step instead of stepping around a stuck row. This is correct by
   construction: it only ever touches THIS task's rows, and only when we're
   the one (re)starting it.

2. recover_stranded_tasks() -- called once when a worker boots (Celery's
   worker_ready signal, see worker.py). Handles the case a killed worker's
   message was genuinely lost rather than redelivered: a Task sitting in
   RUNNING with nothing re-invoking it. We re-enqueue those so they resume.
   Gated on a grace window (settings.stranded_task_grace_seconds) so it does
   NOT race Celery's own acks_late redelivery of a *freshly* crashed task --
   only genuinely stale RUNNING tasks (untouched past the window) get
   re-enqueued.

Known limit (documented, not hidden): a re-run can re-execute a
side-effecting tool call that already happened before the crash (a file
write, a future email send), because tool calls aren't yet idempotent. That's
the next tier of this work, not solved here.
"""

from datetime import datetime, timedelta, timezone

from sqlmodel import select

from agentsys.config import settings
from agentsys.db.models import Subtask, SubtaskStatus, Task, TaskStatus
from agentsys.db.session import get_session

# Subtask states that only ever exist *during* a step's execution. Persisting
# across a fresh graph invocation means the run that set them died.
_IN_FLIGHT_SUBTASK_STATUSES = (SubtaskStatus.RUNNING, SubtaskStatus.NEEDS_REVISION)


def reconcile_orphaned_subtasks(task_id: str) -> int:
    """Mark this task's in-flight-but-stranded subtasks FAILED. Returns how
    many were reconciled (0 in the normal, no-crash case)."""
    with get_session() as session:
        orphaned = session.exec(
            select(Subtask).where(
                Subtask.task_id == task_id,
                Subtask.status.in_(_IN_FLIGHT_SUBTASK_STATUSES),  # type: ignore[attr-defined]
            )
        ).all()
        for subtask in orphaned:
            subtask.status = SubtaskStatus.FAILED
            subtask.output = (subtask.output or "") + "\n[recovery] a worker died mid-step; marked failed on resume."
            subtask.updated_at = datetime.now(timezone.utc)
            session.add(subtask)
        if orphaned:
            session.commit()
        return len(orphaned)


def recover_stranded_tasks() -> list[str]:
    """Re-enqueue Tasks left in RUNNING by a dead worker whose message was not
    redelivered. Returns the ids re-enqueued. Import of run_agent_task is
    local to avoid a circular import (worker imports this module)."""
    from agentsys.worker import run_agent_task

    # Task.updated_at is stored naive-UTC (TIMESTAMP WITHOUT TIME ZONE), so
    # compare against a naive-UTC cutoff to avoid an aware/naive mismatch.
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=settings.stranded_task_grace_seconds)
    with get_session() as session:
        stranded = session.exec(
            select(Task).where(Task.status == TaskStatus.RUNNING, Task.updated_at < cutoff)
        ).all()
        stranded_ids = [t.id for t in stranded]

    for task_id in stranded_ids:
        run_agent_task.delay(task_id)
    return stranded_ids
