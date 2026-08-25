"""Deterministic tests for the graph's control-flow logic — no LLM calls, so
these are fast and fully reproducible. The LLM-driven nodes (sketch/agent_step/
review/synthesize) are covered separately in test_graph_integration.py."""

from sqlmodel import select

from agentsys.config import settings
from datetime import datetime, timedelta, timezone

from agentsys.db.models import Escalation, Subtask, SubtaskStatus, Task, TaskMessage, TaskStatus
from agentsys.db.session import get_session, init_db
from agentsys.graph.nodes import _classify_turn, _load_sketch, agent_step_node, route_entry
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


def test_route_entry_sends_a_fresh_task_to_the_front_door():
    """A new turn is triaged first -- it may need no machinery at all."""
    task_id = _make_task()
    assert route_entry({"task_id": task_id}) == "triage"


def test_route_entry_skips_triage_when_disabled():
    """ENABLE_TRIAGE=false must restore the previous behaviour exactly."""
    task_id = _make_task()
    with_triage_off = {"task_id": task_id}
    settings.enable_triage = False
    try:
        assert route_entry(with_triage_off) == "sketch"
    finally:
        settings.enable_triage = True


def test_route_entry_resumes_mid_loop_without_re_triaging():
    """Work already happened this turn, so the user has committed to it --
    re-classifying would add a call and could only get it wrong."""
    task_id = _make_task()
    _make_subtask(task_id, 0, SubtaskStatus.DONE)
    assert route_entry({"task_id": task_id}) == "agent_step"


def test_route_entry_resumes_a_task_awaiting_approval_without_triage():
    """A human just decided an escalation; that decision is the instruction,
    not something to reclassify."""
    task_id = _make_task()
    with get_session() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.AWAITING_APPROVAL
        session.add(task)
        session.commit()
    assert route_entry({"task_id": task_id}) == "agent_step"


def test_route_entry_triages_a_follow_up_turn_on_an_existing_task():
    """The case triage helps most: a completed task gets "thanks". Prior
    subtasks exist, but none belong to THIS turn, so it is a new turn."""
    task_id = _make_task()
    _make_subtask(task_id, 0, SubtaskStatus.DONE)
    with get_session() as session:
        session.add(
            TaskMessage(task_id=task_id, role="user", content="thanks!",
                        created_at=datetime.now(timezone.utc) + timedelta(seconds=5))
        )
        session.commit()
    assert route_entry({"task_id": task_id}) == "triage"


def test_triage_fails_open_to_the_full_path(monkeypatch):
    """A broken classifier must cost latency, never capability."""
    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("agentsys.graph.nodes.structured_complete", boom)
    decision, completion = _classify_turn("anything", "")
    assert decision.route == "full"
    assert completion is None


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
