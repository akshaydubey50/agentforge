"""Deterministic tests for the graph's control-flow logic — no LLM calls, so
these are fast and fully reproducible. The LLM-driven nodes (plan/execute/
review/synthesize) are covered separately in test_graph_integration.py."""

import uuid

from sqlmodel import select

from agentsys.db.models import Escalation, Subtask, SubtaskStatus, Task
from agentsys.db.session import get_session, init_db
from agentsys.graph.nodes import route_entry, select_subtask_node


def setup_module() -> None:
    init_db()


def _make_task(request_text: str = "test") -> str:
    with get_session() as session:
        task = Task(request_text=request_text)
        session.add(task)
        session.commit()
        return task.id


def _make_subtask(task_id: str, position: int, status: SubtaskStatus, depends_on: list[str] | None = None) -> Subtask:
    with get_session() as session:
        subtask = Subtask(
            task_id=task_id, position=position, description=f"subtask {position}",
            status=status, depends_on=depends_on or [],
        )
        session.add(subtask)
        session.commit()
        session.refresh(subtask)
        return subtask


def test_route_entry_plans_a_fresh_task():
    task_id = _make_task()
    assert route_entry({"task_id": task_id}) == "plan"


def test_route_entry_resumes_a_task_with_existing_subtasks():
    task_id = _make_task()
    _make_subtask(task_id, 0, SubtaskStatus.DONE)
    assert route_entry({"task_id": task_id}) == "select_subtask"


def test_select_subtask_picks_the_dependency_ready_one():
    task_id = _make_task()
    first = _make_subtask(task_id, 0, SubtaskStatus.DONE)
    _make_subtask(task_id, 1, SubtaskStatus.PENDING, depends_on=[first.id])
    _make_subtask(task_id, 2, SubtaskStatus.PENDING, depends_on=["nonexistent-id-not-done"])

    result = select_subtask_node({"task_id": task_id})

    assert result["route"] == "execute"
    with get_session() as session:
        picked = session.get(Subtask, result["current_subtask_id"])
    assert picked.position == 1
    assert picked.status == SubtaskStatus.RUNNING


def test_select_subtask_routes_to_synthesize_when_all_terminal():
    task_id = _make_task()
    _make_subtask(task_id, 0, SubtaskStatus.DONE)
    _make_subtask(task_id, 1, SubtaskStatus.SKIPPED)

    result = select_subtask_node({"task_id": task_id})
    assert result["route"] == "synthesize"


def test_select_subtask_escalates_when_nothing_is_ready():
    task_id = _make_task()
    # A pending subtask whose only dependency will never complete (not in the
    # done set and not itself runnable) — this is the "stuck" case.
    fake_dep_id = str(uuid.uuid4())
    _make_subtask(task_id, 0, SubtaskStatus.FAILED)  # terminal, not "done", so dep never satisfied
    _make_subtask(task_id, 1, SubtaskStatus.PENDING, depends_on=[fake_dep_id])

    result = select_subtask_node({"task_id": task_id})
    assert result["route"] == "escalate"

    with get_session() as session:
        escalations = session.exec(select(Escalation).where(Escalation.task_id == task_id)).all()
    assert len(escalations) == 1
    assert escalations[0].subtask_id is None
