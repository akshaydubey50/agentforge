"""Phase 4 verification through the existing graph seams.

DB-backed but deterministic: no LLM calls are allowed in these cases because
deterministic verification settles the route before the reviewer fallback.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlmodel import select

from agentsys import deadcalls
from agentsys.config import settings
from agentsys.db.models import Review, Subtask, SubtaskStatus, Task, ToolCall
from agentsys.db.session import get_session, init_db
from agentsys.graph import nodes
from conftest import get_test_owner_id


def setup_module() -> None:
    init_db()


def _task_and_subtask(criteria: str, *, tool: str = "file_io") -> tuple[str, str]:
    with get_session() as session:
        task = Task(request_text="phase 4", owner_id=get_test_owner_id())
        session.add(task)
        session.commit()
        subtask = Subtask(
            task_id=task.id,
            position=0,
            description="verify the thing",
            success_criteria=criteria,
            assigned_tool=tool,
            status=SubtaskStatus.RUNNING,
            output="tool output",
            attempt_count=1,
        )
        session.add(subtask)
        session.commit()
        return task.id, subtask.id


def _add_call(subtask_id: str, tool: str, args: dict, output: dict, *, success: bool = True) -> None:
    with get_session() as session:
        session.add(
            ToolCall(
                subtask_id=subtask_id,
                tool_name=tool,
                input=args,
                output=output,
                success=success,
                latency_ms=1,
            )
        )
        session.commit()


def test_deterministic_pass_marks_done_without_reviewer_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(nodes, "structured_complete", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    task_id, subtask_id = _task_and_subtask("file_exists: report.txt")
    written = tmp_path / task_id / "report.txt"
    written.parent.mkdir(parents=True)
    written.write_text("hello", encoding="utf-8")
    _add_call(
        subtask_id,
        "file_io",
        {"action": "write", "path": "report.txt", "content": "hello", "task_id": task_id},
        {"path": str(written)},
    )

    route = nodes._review_subtask(task_id, subtask_id, tool_success=True)

    assert route == "agent_step"
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        review = session.exec(select(Review).where(Review.subtask_id == subtask_id)).first()
    assert subtask.status is SubtaskStatus.DONE
    assert review.verdict == "pass"


def test_verification_replan_marks_failed_and_records_dead_call(monkeypatch):
    monkeypatch.setattr(nodes, "structured_complete", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    task_id, subtask_id = _task_and_subtask("result_count >= 1", tool="knowledge_search")
    args = {"question": "internal policy"}
    _add_call(subtask_id, "knowledge_search", args, {"results": []})

    route = nodes._review_subtask(task_id, subtask_id, tool_success=True)

    assert route == "agent_step"
    assert deadcalls.is_dead(task_id, "knowledge_search", args)
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
    assert subtask.status is SubtaskStatus.FAILED
    assert "result count 0 < required 1" in subtask.output


def test_failed_step_context_is_visible_to_replanner():
    task_id, subtask_id = _task_and_subtask("result_count >= 1", tool="knowledge_search")
    with get_session() as session:
        subtask = session.get(Subtask, subtask_id)
        subtask.status = SubtaskStatus.FAILED
        subtask.output = "[verification] replan: result count 0 < required 1"
        session.add(subtask)
        session.commit()

    context = nodes._gather_prior_context(task_id)

    assert "[failed]" in context
    assert "result count 0 < required 1" in context
