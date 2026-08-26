from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlmodel import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentsys import events, execution, recovery, runtime
from agentsys.db.models import Subtask, SubtaskStatus, Task, TaskStatus, ToolCall
from agentsys.db.session import get_session, init_db
from agentsys.graph import runner
from agentsys.main import app
from agentsys.policy import ActionType
from agentsys.tools.base import Tool, ToolResult
from conftest import get_test_owner_id


def setup_module() -> None:
    init_db()


@pytest.fixture(autouse=True)
def _reset_runtime():
    runtime.clear_shutdown()
    yield
    runtime.clear_shutdown()


class _EffectTool(Tool):
    name = "phase8_effect"
    description = "test double"
    action_type = ActionType.EXTERNAL_WRITE

    def __init__(self) -> None:
        self.calls = 0

    def validate_args(self, proposed: dict) -> dict:
        return dict(proposed)

    def run(self, **kwargs) -> ToolResult:
        self.calls += 1
        return ToolResult(success=True, output={"ok": True, "calls": self.calls})


def _task(status: TaskStatus = TaskStatus.PENDING, *, updated_at: datetime | None = None) -> str:
    with get_session() as session:
        task = Task(
            request_text="phase 8",
            owner_id=get_test_owner_id(),
            status=status,
            updated_at=updated_at or datetime.now(timezone.utc),
        )
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _subtask(task_id: str, status: SubtaskStatus = SubtaskStatus.RUNNING) -> str:
    with get_session() as session:
        subtask = Subtask(task_id=task_id, position=0, description="phase 8", status=status)
        session.add(subtask)
        session.commit()
        session.refresh(subtask)
        return subtask.id


def test_two_workers_cannot_enter_the_same_task_graph(monkeypatch):
    task_id = _task(TaskStatus.RUNNING)
    started = Event()
    release = Event()
    calls: list[str] = []

    class FakeGraph:
        def invoke(self, state, config):
            calls.append(state["task_id"])
            started.set()
            assert release.wait(5), "first worker was never released"

    monkeypatch.setattr(runner, "get_graph", lambda: FakeGraph())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runner.run_task, task_id)
        assert started.wait(5), "first worker never entered the graph"
        second = pool.submit(runner.run_task, task_id)
        assert second.result(timeout=5) is False
        release.set()
        assert first.result(timeout=5) is True

    assert calls == [task_id]


def test_postgres_unavailable_blocks_external_write_before_tool_runs(monkeypatch):
    tool = _EffectTool()

    class BrokenSession:
        def __enter__(self):
            raise RuntimeError("postgres unavailable")

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(execution, "get_session", lambda: BrokenSession())

    with pytest.raises(RuntimeError, match="postgres unavailable"):
        execution.execute_tool(
            tool,
            {"x": 1},
            task_id="task-postgres-down",
            subtask_id="subtask-postgres-down",
            action_type=ActionType.EXTERNAL_WRITE,
        )

    assert tool.calls == 0


def test_recovery_reenqueue_is_bounded_and_idempotent(monkeypatch):
    old = datetime(1970, 1, 1)
    task_id = _task(TaskStatus.RUNNING, updated_at=old)
    enqueued: list[str] = []

    import agentsys.worker as worker

    monkeypatch.setattr(worker.run_agent_task, "delay", lambda tid: enqueued.append(tid))
    monkeypatch.setattr(recovery.settings, "recovery_batch_size", 1)

    assert recovery.recover_stranded_tasks() == [task_id]
    assert enqueued == [task_id]
    assert task_id not in recovery.recover_stranded_tasks()


def test_recovery_finds_lost_pending_task(monkeypatch):
    old = datetime(1970, 1, 2)
    task_id = _task(TaskStatus.PENDING, updated_at=old)
    enqueued: list[str] = []

    import agentsys.worker as worker

    monkeypatch.setattr(worker.run_agent_task, "delay", lambda tid: enqueued.append(tid))
    monkeypatch.setattr(recovery.settings, "recovery_batch_size", 1)

    assert recovery.recover_stranded_tasks() == [task_id]
    assert enqueued == [task_id]


def test_find_stuck_tasks_uses_state_age_not_status_alone():
    old = datetime(1970, 1, 3)
    stale = _task(TaskStatus.RUNNING, updated_at=old)
    active = _task(TaskStatus.RUNNING, updated_at=datetime.now(timezone.utc))

    findings = recovery.find_stuck_tasks(limit=1)
    by_id = {item["task_id"]: item for item in findings}

    assert by_id[stale]["reason"] == "stale_running"
    assert active not in by_id


def test_redis_event_outage_does_not_mutate_or_corrupt_durable_task(monkeypatch):
    task_id = _task(TaskStatus.RUNNING)

    class BrokenRedis:
        def publish(self, *_args, **_kwargs):
            raise RuntimeError("redis unavailable")

    monkeypatch.setattr(events, "get_redis", lambda: BrokenRedis())

    events.publish(task_id, "task_status", {"status": "running"})

    with get_session() as session:
        assert session.get(Task, task_id).status is TaskStatus.RUNNING


def test_ready_reports_dependency_failure_without_business_mutation(monkeypatch):
    from sqlalchemy import func
    import agentsys.main as main

    def _count() -> int:
        with get_session() as session:
            return session.exec(select(func.count()).select_from(Task)).one()

    def _boom():
        raise RuntimeError("chroma down")

    monkeypatch.setattr(main, "get_chroma_client", _boom)
    client = TestClient(app)
    before = _count()

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert _count() == before


def test_graceful_shutdown_guard_stops_new_work():
    user_id = get_test_owner_id()
    runtime.request_shutdown()

    with pytest.raises(HTTPException) as exc:
        runtime.enforce_accepting_work(user_id)

    assert exc.value.status_code == 503


def test_active_task_capacity_is_enforced(monkeypatch):
    user_id = get_test_owner_id()
    monkeypatch.setattr(runtime.settings, "max_active_tasks", 0)

    with pytest.raises(HTTPException) as exc:
        runtime.enforce_accepting_work(user_id)

    assert exc.value.status_code == 429


def test_ambiguous_non_retryable_effect_is_not_replayed_after_recovery():
    tool = _EffectTool()
    task_id = _task(TaskStatus.RUNNING)
    subtask_id = _subtask(task_id)
    key = execution.effect_key(tool.name, {"x": 1}, task_id=task_id)
    execution._open_call(subtask_id, tool.name, {"x": 1}, key)

    outcome = execution.execute_tool(
        tool,
        {"x": 1},
        task_id=task_id,
        subtask_id=subtask_id,
        action_type=ActionType.EXTERNAL_WRITE,
    )

    assert outcome.refused is True
    assert tool.calls == 0


def test_recovery_never_turns_ambiguous_external_effect_into_success():
    task_id = _task(TaskStatus.RUNNING)
    subtask_id = _subtask(task_id)
    key = execution.effect_key("gmail_create_draft", {"to": "a@example.com"}, task_id=task_id)
    execution._open_call(subtask_id, "gmail_create_draft", {"to": "a@example.com"}, key)

    recovery.reconcile_orphaned_subtasks(task_id)

    with get_session() as session:
        row = session.exec(select(ToolCall).where(ToolCall.subtask_id == subtask_id)).first()
        subtask = session.get(Subtask, subtask_id)
    assert row.success is None
    assert subtask.status is SubtaskStatus.FAILED
