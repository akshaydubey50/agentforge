"""Deterministic tests for the graph's control-flow logic — no LLM calls, so
these are fast and fully reproducible. The LLM-driven nodes (sketch/agent_step/
review/synthesize) are covered separately in test_graph_integration.py."""

from sqlmodel import select

from agentsys.config import settings
from agentsys.db.models import Escalation, Subtask, SubtaskStatus, Task
from agentsys.db.session import get_session, init_db
from agentsys.graph.nodes import _load_sketch, agent_step_node, route_entry
from conftest import get_test_owner_id


def setup_module() -> None:
    init_db()


def _make_task(request_text: str = "test") -> str:
    with get_session() as session:
        task = Task(request_text=request_text, owner_id=get_test_owner_id())
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


def test_route_entry_sketches_a_fresh_task():
    task_id = _make_task()
    assert route_entry({"task_id": task_id}) == "sketch"


def test_route_entry_resumes_a_task_with_existing_subtasks():
    task_id = _make_task()
    _make_subtask(task_id, 0, SubtaskStatus.DONE)
    assert route_entry({"task_id": task_id}) == "agent_step"


def test_load_sketch_falls_back_when_no_sketch_span_exists():
    task_id = _make_task()
    assert _load_sketch(task_id) == "(no sketch available)"


def test_agent_step_escalates_when_step_budget_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "max_task_steps", 2)
    task_id = _make_task()
    _make_subtask(task_id, 0, SubtaskStatus.DONE)
    _make_subtask(task_id, 1, SubtaskStatus.DONE)

    result = agent_step_node({"task_id": task_id})

    assert result["route"] == "escalate"
    with get_session() as session:
        escalations = session.exec(select(Escalation).where(Escalation.task_id == task_id)).all()
    assert len(escalations) == 1
    assert escalations[0].subtask_id is None
    assert "max_task_steps" in escalations[0].reason
