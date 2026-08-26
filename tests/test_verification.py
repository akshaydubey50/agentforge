"""Phase 4 deterministic verification rules.

No model, no network, no running graph. These are the checks that should stay
hard-gated because they are pure or local filesystem evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentsys.config import settings
from agentsys.db.models import Subtask, SubtaskStatus, ToolCall
from agentsys.verification import VerificationRoute, verify_goal, verify_step


def _subtask(criteria: str | None = None) -> Subtask:
    return Subtask(
        task_id="task-1",
        position=0,
        description="do it",
        success_criteria=criteria,
        status=SubtaskStatus.RUNNING,
    )


def _call(tool: str, args: dict, output: dict, success: bool = True) -> ToolCall:
    return ToolCall(
        subtask_id="sub-1",
        tool_name=tool,
        input=args,
        output=output,
        success=success,
        latency_ms=1,
    )


def test_file_write_verifies_file_exists_and_content(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    task_id = "task-1"
    path = tmp_path / task_id / "report.txt"
    path.parent.mkdir(parents=True)
    path.write_text("hello", encoding="utf-8")

    result = verify_step(
        task_id,
        _subtask("file_exists: report.txt"),
        _call("file_io", {"action": "write", "path": "report.txt", "content": "hello"}, {"path": str(path)}),
        tool_success=True,
    )

    assert result.route is VerificationRoute.PASS
    assert result.verified is True


def test_file_write_success_but_missing_file_routes_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))

    result = verify_step(
        "task-1",
        _subtask("file_exists: report.txt"),
        _call("file_io", {"action": "write", "path": "report.txt", "content": "hello"}, {"path": "x"}),
        tool_success=True,
    )

    assert result.route is VerificationRoute.RETRY
    assert result.retryable is True


def test_unsupported_file_write_criteria_falls_back_to_review(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    path = tmp_path / "task-1" / "report.txt"
    path.parent.mkdir(parents=True)
    path.write_text("hello", encoding="utf-8")

    result = verify_step(
        "task-1",
        _subtask("run this criterion somehow"),
        _call("file_io", {"action": "write", "path": "report.txt", "content": "hello"}, {"path": str(path)}),
        tool_success=True,
    )

    assert result.route is VerificationRoute.REVIEW
    assert result.method == "semantic_fallback"


def test_retryable_tool_failure_routes_retry():
    result = verify_step(
        "task-1",
        _subtask("nonempty"),
        _call("web_search", {"query": "x"}, {"error": "connection reset by peer"}, success=False),
        tool_success=False,
    )

    assert result.route is VerificationRoute.RETRY
    assert result.retryable is True


def test_successful_empty_result_with_required_count_routes_replan():
    result = verify_step(
        "task-1",
        _subtask("result_count >= 1"),
        _call("knowledge_search", {"question": "internal policy"}, {"results": []}),
        tool_success=True,
    )

    assert result.route is VerificationRoute.REPLAN
    assert result.needs_replan is True


def test_gmail_search_messages_count_as_results():
    result = verify_step(
        "task-1",
        _subtask("result_count > 0"),
        _call("gmail_search", {"query": "from:recruiter@example.com"}, {"messages": [{"id": "msg-1"}]}),
        tool_success=True,
    )

    assert result.route is VerificationRoute.PASS
    assert result.verified is True


def test_empty_result_without_count_uses_reviewer_fallback():
    result = verify_step(
        "task-1",
        _subtask(None),
        _call("web_search", {"query": "x"}, {"results": []}),
        tool_success=True,
    )

    assert result.route is VerificationRoute.REVIEW
    assert result.method == "semantic_fallback"


def test_ambiguous_effect_refusal_routes_human():
    result = verify_step(
        "task-1",
        _subtask("semantic"),
        _call(
            "mcp_unknown_delete",
            {"id": "r1"},
            {"error": "outcome is unknown and it was not performed again"},
            success=False,
        ),
        tool_success=False,
    )

    assert result.route is VerificationRoute.REQUIRE_HUMAN
    assert result.needs_human is True


def test_goal_verification_passes_when_requested_file_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    task_id = "task-1"
    path = tmp_path / task_id / "report.md"
    path.parent.mkdir(parents=True)
    path.write_text("done", encoding="utf-8")

    result = verify_goal(
        task_id,
        "Create report.md",
        [Subtask(task_id=task_id, position=0, description="write", status=SubtaskStatus.DONE)],
    )

    assert result.route is VerificationRoute.PASS
    assert result.verified is True


def test_goal_verification_fails_when_requested_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))

    result = verify_goal(
        "task-1",
        "Create report.md",
        [Subtask(task_id="task-1", position=0, description="write", status=SubtaskStatus.DONE)],
    )

    assert result.route is VerificationRoute.REPLAN
    assert result.needs_replan is True


def test_goal_verification_passes_completed_gmail_draft_despite_superseded_failure():
    failed_search = Subtask(
        task_id="task-1",
        position=0,
        description="broad search",
        assigned_tool="gmail_search",
        status=SubtaskStatus.FAILED,
        output='{"messages": [{"id": "msg-1"}]}',
    )
    draft = Subtask(
        task_id="task-1",
        position=1,
        description="create draft",
        assigned_tool="gmail_create_draft",
        status=SubtaskStatus.DONE,
        output='{"draft_id": "draft-1", "to": "recruiter@example.com"}',
    )

    result = verify_goal("task-1", "Prepare a reply draft email", [failed_search, draft])

    assert result.route is VerificationRoute.PASS
    assert result.verified is True


def test_semantic_goal_uses_reviewer_fallback():
    result = verify_goal(
        "task-1",
        "Research the top trends and summarize them",
        [Subtask(task_id="task-1", position=0, description="research", status=SubtaskStatus.DONE)],
    )

    assert result.route is VerificationRoute.REVIEW
    assert result.method == "semantic_fallback"
