"""Small runtime guards for overload and graceful shutdown."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from sqlalchemy import func, or_
from sqlmodel import select

from agentsys.config import settings
from agentsys.db.models import Task, TaskStatus
from agentsys.db.session import get_session

_shutting_down = False


def request_shutdown() -> None:
    global _shutting_down
    _shutting_down = True


def clear_shutdown() -> None:
    global _shutting_down
    _shutting_down = False


def is_shutting_down() -> bool:
    return _shutting_down


def active_task_counts(user_id: str | None = None) -> dict[str, int]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    running_cutoff = now - timedelta(seconds=settings.stranded_task_grace_seconds)
    pending_cutoff = now - timedelta(seconds=settings.stale_pending_task_grace_seconds)
    active_filter = or_(
        (Task.status == TaskStatus.RUNNING) & (Task.updated_at >= running_cutoff),
        (Task.status == TaskStatus.PENDING) & (Task.updated_at >= pending_cutoff),
    )
    with get_session() as session:
        total = session.exec(
            select(func.count())
            .select_from(Task)
            .where(active_filter, Task.is_eval == False)  # noqa: E712
        ).one()
        per_user = 0
        if user_id is not None:
            per_user = session.exec(
                select(func.count())
                .select_from(Task)
                .where(Task.owner_id == user_id, active_filter, Task.is_eval == False)  # noqa: E712
            ).one()
    return {"active_tasks": int(total or 0), "active_tasks_for_user": int(per_user or 0)}


def enforce_accepting_work(user_id: str) -> None:
    if _shutting_down:
        raise HTTPException(status_code=503, detail="service is shutting down; retry shortly")
    counts = active_task_counts(user_id)
    if counts["active_tasks"] >= settings.max_active_tasks:
        raise HTTPException(status_code=429, detail="system is at active task capacity; retry shortly")
    if counts["active_tasks_for_user"] >= settings.max_active_tasks_per_user:
        raise HTTPException(status_code=429, detail="user is at active task capacity; wait for a task to finish")
