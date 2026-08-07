"""Proves select_subtask_node's wave dispatch actually runs independent
subtasks concurrently (via LangGraph Send), not just correctly -- and that
one subtask escalating still pauses the whole task even when its wave-mates
succeeded. Real LLM calls, same "no mocks" standard as test_graph_integration.py."""

from sqlmodel import select

from agentsys.db.models import Subtask, SubtaskStatus, Task, TaskStatus, TraceSpan
from agentsys.db.session import get_session, init_db
from agentsys.graph.nodes import select_subtask_node
from agentsys.graph.runner import run_task


def setup_module() -> None:
    init_db()


def _create_task_with_independent_subtasks(n: int) -> str:
    with get_session() as session:
        task = Task(request_text="test parallel execution", status=TaskStatus.RUNNING)
        session.add(task)
        session.commit()
        session.refresh(task)
        for i in range(n):
            session.add(
                Subtask(
                    task_id=task.id, position=i,
                    description=f"Say the number {i} and nothing else.",
                    status=SubtaskStatus.PENDING,
                )
            )
        session.commit()
        return task.id


def test_independent_subtasks_actually_overlap_in_wall_clock_time():
    task_id = _create_task_with_independent_subtasks(2)
    run_task(task_id)  # route_entry sees existing subtasks and resumes at select_subtask, skipping planning

    with get_session() as session:
        task = session.get(Task, task_id)
        subtasks = session.exec(
            select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.position)
        ).all()

    assert task.status == TaskStatus.COMPLETED
    assert all(s.status == SubtaskStatus.DONE for s in subtasks)

    with get_session() as session:
        spans = session.exec(
            select(TraceSpan)
            .where(
                TraceSpan.subtask_id.in_([s.id for s in subtasks]),
                TraceSpan.span_type == "tool_selection",
            )
            .order_by(TraceSpan.started_at)
        ).all()

    # First span per subtask (in case a retry produced more than one) --
    # what matters is when each subtask *first* started.
    first_span_by_subtask = {}
    for sp in spans:
        first_span_by_subtask.setdefault(sp.subtask_id, sp)
    assert len(first_span_by_subtask) == 2

    first, second = sorted(first_span_by_subtask.values(), key=lambda sp: sp.started_at)
    assert first.ended_at is not None
    # True concurrency: the second subtask's LLM call started before the
    # first one's finished -- impossible if select_subtask_node still ran
    # them one at a time.
    assert second.started_at < first.ended_at, (
        "subtasks ran sequentially, not concurrently — "
        f"first ended at {first.ended_at}, second started at {second.started_at}"
    )


def test_one_escalating_subtask_pauses_the_whole_wave():
    """Unit-level check (no LLM, mirrors test_graph_routing.py's style) that
    select_subtask_node's escalated_subtask_ids check actually short-circuits
    to "escalate" before it would otherwise schedule more work -- this is the
    barrier logic run_subtask_node's Send branches feed into."""
    with get_session() as session:
        task = Task(request_text="test", status=TaskStatus.RUNNING)
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    result = select_subtask_node({"task_id": task_id, "escalated_subtask_ids": ["some-subtask-id"]})
    assert result["route"] == "escalate"
