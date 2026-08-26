"""Cooperative cancellation for a running task.

A cancel request can't preempt a worker mid-step -- the worker is a separate
process, usually blocked on an LLM call. What it can do is set a flag the
worker checks at each step boundary (see graph/nodes.py's _check_cancelled,
called at the top of sketch_node and agent_step_node), so a cancelled task
stops before starting its NEXT expensive step rather than running to
completion and billing the whole way.

The flag lives in Redis rather than being read off Task.status because the
worker would otherwise need a Postgres round-trip per step just to ask "am I
still wanted", and because the two carry different meanings: Task.status is
what the user sees, the flag is the instruction to the worker. The API sets
both (see main.py's cancel_task) -- status immediately, so the dashboard
reflects the cancel right away instead of waiting for the worker to notice.
"""

from __future__ import annotations

from agentsys.memory.short_term import get_redis

_CANCEL_TTL_S = 60 * 60 * 24
"""Long enough to outlive any real run (task_time_limit_seconds is far
shorter), short enough that flags don't accumulate forever. The flag is
also deleted explicitly on a fresh (re)start of a task -- see clear_cancel."""


class TaskCancelled(Exception):
    """Raised inside the graph when a cancel was requested. Caught in
    worker.py's run_agent_task, which returns cleanly WITHOUT touching
    task.status -- the API already set it to CANCELLED, and overwriting it
    with FAILED would misreport a deliberate stop as an error."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"task {task_id} was cancelled")


def _key(task_id: str) -> str:
    return f"cancel:{task_id}"


def request_cancel(task_id: str) -> None:
    get_redis().setex(_key(task_id), _CANCEL_TTL_S, "1")


def is_cancel_requested(task_id: str) -> bool:
    """Fails OPEN (returns False) on a Redis error: a cancellation check that
    throws would turn a Redis blip into a failed task, which is strictly
    worse than a task that misses one cancel and gets caught by the wall-clock
    limit instead."""
    try:
        return bool(get_redis().exists(_key(task_id)))
    except Exception:  # noqa: BLE001 -- see fail-open note above
        return False


def clear_cancel(task_id: str) -> None:
    """Clears a stale flag when a task is deliberately (re)started -- e.g. a
    follow-up message or an approved escalation resuming a task that was
    cancelled earlier. Without this, the resumed run would immediately
    cancel itself on the previous flag."""
    try:
        get_redis().delete(_key(task_id))
    except Exception:  # noqa: BLE001 -- best-effort, same reasoning as above
        pass
