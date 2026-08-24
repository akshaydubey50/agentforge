"""Context-engineering and loop-engineering tests: artifact spillover,
recency-window compaction, the dead-call ledger, the unproductive-step
counter, and the cost ceiling.

All deterministic -- no LLM calls. These cover the harness behavior that the
measured 11-step failure exposed (164,365 prompt tokens for 4,593 output, with
36% of steps re-attempting already-impossible tool calls).
"""

from __future__ import annotations

import json
import uuid

import pytest

from agentsys import artifacts, deadcalls
from agentsys.config import settings
from agentsys.db.models import LlmCall, Subtask, SubtaskStatus, Task
from agentsys.db.session import get_session, init_db
from agentsys.graph.nodes import (
    _gather_prior_context,
    _record_step_productivity,
    _task_cost_usd,
    _unproductive_streak,
)
from agentsys.memory import short_term
from conftest import get_test_owner_id


@pytest.fixture
def task_id() -> str:
    init_db()
    with get_session() as session:
        task = Task(request_text="context test", owner_id=get_test_owner_id())
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _add_subtask(task_id: str, position: int, description: str, output: str) -> str:
    with get_session() as session:
        subtask = Subtask(
            task_id=task_id,
            position=position,
            description=description,
            output=output,
            status=SubtaskStatus.DONE,
        )
        session.add(subtask)
        session.commit()
        session.refresh(subtask)
        return subtask.id


# --------------------------------------------------------------------------
# Artifact spillover
# --------------------------------------------------------------------------


def test_spill_writes_full_output_and_returns_pointer(task_id: str, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    big = "A" * 30_000

    pointer = artifacts.spill(task_id, "sub-1", "google_drive_read", big)

    assert pointer["_truncated"] is True
    assert pointer["total_chars"] == 30_000
    assert len(pointer["preview"]) == settings.tool_output_preview_chars
    # The path is relative to the task workspace, which is exactly what
    # file_io's `path` argument takes -- the agent can paste it straight back.
    assert pointer["full_output_path"].startswith(f"{artifacts.ARTIFACT_DIRNAME}/")
    written = artifacts.artifact_dir(task_id) / pointer["full_output_path"].split("/", 1)[1]
    assert written.read_text(encoding="utf-8") == big


def test_spilled_pointer_is_dramatically_smaller_than_the_payload(task_id: str, tmp_path, monkeypatch):
    """The whole point: what lands in the prompt must be a small fraction of
    what the tool returned."""
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    big = "B" * 30_000

    pointer_text = json.dumps(artifacts.spill(task_id, "sub-1", "t", big))

    assert len(pointer_text) < len(big) / 10


def test_spill_sanitizes_tool_name_into_the_filename(task_id: str, tmp_path, monkeypatch):
    """MCP servers can contribute tool names from third-party config, so a
    name reaching the filesystem must not be able to escape the directory."""
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))

    pointer = artifacts.spill(task_id, "sub-1", "../../evil", "x" * 10)

    assert ".." not in pointer["full_output_path"]
    assert (artifacts.artifact_dir(task_id) / pointer["full_output_path"].split("/", 1)[1]).is_file()


# --------------------------------------------------------------------------
# Recency-window compaction
# --------------------------------------------------------------------------


def test_recent_steps_stay_full_and_older_steps_truncate(task_id: str, monkeypatch):
    monkeypatch.setattr(settings, "context_recent_steps_full", 2)
    monkeypatch.setattr(settings, "context_older_step_chars", 50)

    oldest = "O" * 5_000
    for position, output in enumerate([oldest, "M" * 5_000, "R1" * 2_500, "R2" * 2_500]):
        _add_subtask(task_id, position, f"step {position}", output)

    context = _gather_prior_context(task_id)

    # Two most recent verbatim, two older ones cut down to the marker.
    assert "R1" * 2_500 in context
    assert "R2" * 2_500 in context
    assert oldest not in context
    assert "[truncated, 5000 chars total" in context


def test_short_older_steps_are_not_truncated(task_id: str, monkeypatch):
    monkeypatch.setattr(settings, "context_recent_steps_full", 1)
    monkeypatch.setattr(settings, "context_older_step_chars", 300)
    _add_subtask(task_id, 0, "tiny", "just a short result")
    _add_subtask(task_id, 1, "recent", "also short")

    context = _gather_prior_context(task_id)

    assert "just a short result" in context
    assert "truncated" not in context


def test_prior_context_excludes_the_current_subtask(task_id: str):
    _add_subtask(task_id, 0, "earlier", "earlier output")
    current = _add_subtask(task_id, 1, "current", "current output")

    context = _gather_prior_context(task_id, current_subtask_id=current)

    assert "earlier output" in context
    assert "current output" not in context


def test_empty_prior_context_has_a_readable_placeholder(task_id: str):
    assert _gather_prior_context(task_id) == "(no prior context yet)"


# --------------------------------------------------------------------------
# Dead-call ledger
# --------------------------------------------------------------------------


def test_identical_failed_call_is_blocked_on_repeat(task_id: str):
    kwargs = {"file_id": "abc123"}
    assert deadcalls.is_dead(task_id, "google_drive_read", kwargs) is False

    deadcalls.record_failure(task_id, "google_drive_read", kwargs)

    assert deadcalls.is_dead(task_id, "google_drive_read", kwargs) is True


def test_different_args_and_different_tools_are_not_blocked(task_id: str):
    deadcalls.record_failure(task_id, "google_drive_read", {"file_id": "abc123"})

    assert deadcalls.is_dead(task_id, "google_drive_read", {"file_id": "different"}) is False
    assert deadcalls.is_dead(task_id, "gmail_read", {"file_id": "abc123"}) is False


def test_arg_key_order_does_not_defeat_matching(task_id: str):
    deadcalls.record_failure(task_id, "web_search", {"query": "x", "max_results": 5})

    assert deadcalls.is_dead(task_id, "web_search", {"max_results": 5, "query": "x"}) is True


def test_injected_plumbing_keys_are_ignored_when_matching(task_id: str):
    """user_id/task_id/subtask_id are injected by _execute_subtask, not
    chosen by the model -- if they counted, the same logical call would
    never match itself across steps."""
    deadcalls.record_failure(task_id, "gmail_read", {"message_id": "m1", "user_id": "user-a"})

    assert deadcalls.is_dead(task_id, "gmail_read", {"message_id": "m1", "user_id": "user-b"}) is True


def test_ledger_is_scoped_per_task(task_id: str):
    other_task = f"other-{uuid.uuid4()}"
    deadcalls.record_failure(task_id, "web_search", {"query": "x"})

    assert deadcalls.is_dead(other_task, "web_search", {"query": "x"}) is False


def test_describe_lists_dead_calls_for_the_prompt(task_id: str):
    assert deadcalls.describe(task_id) == ""

    deadcalls.record_failure(task_id, "google_drive_read", {"file_id": "abc123"})
    described = deadcalls.describe(task_id)

    assert "google_drive_read" in described
    assert "abc123" in described
    assert "ALREADY FAILED" in described


def test_recording_the_same_failure_twice_does_not_duplicate(task_id: str):
    deadcalls.record_failure(task_id, "web_search", {"query": "x"})
    deadcalls.record_failure(task_id, "web_search", {"query": "x"})

    assert deadcalls.describe(task_id).count("web_search") == 1


# --------------------------------------------------------------------------
# Progress detection
# --------------------------------------------------------------------------


def test_unproductive_streak_increments_and_resets(task_id: str):
    assert _unproductive_streak(task_id) == 0

    _record_step_productivity(task_id, productive=False)
    _record_step_productivity(task_id, productive=False)
    assert _unproductive_streak(task_id) == 2

    # One good step means the agent found a way forward -- the budget for
    # exploring resets with it.
    _record_step_productivity(task_id, productive=True)
    assert _unproductive_streak(task_id) == 0


def test_short_term_clear_wipes_loop_state(task_id: str):
    """Both the ledger and the streak live in the task's working memory, so
    synthesize_node's short_term.clear must take them with it."""
    deadcalls.record_failure(task_id, "web_search", {"query": "x"})
    _record_step_productivity(task_id, productive=False)

    short_term.clear(task_id)

    assert deadcalls.is_dead(task_id, "web_search", {"query": "x"}) is False
    assert _unproductive_streak(task_id) == 0


# --------------------------------------------------------------------------
# Cost ceiling
# --------------------------------------------------------------------------


def test_task_cost_sums_llm_calls(task_id: str):
    assert _task_cost_usd(task_id) == 0.0

    with get_session() as session:
        for cost_usd in (0.01, 0.02, 0.045):
            session.add(
                LlmCall(
                    task_id=task_id, purpose="agent_step", model="test",
                    prompt_tokens=10, completion_tokens=5, cost_usd=cost_usd,
                )
            )
        session.commit()

    assert _task_cost_usd(task_id) == pytest.approx(0.075)
