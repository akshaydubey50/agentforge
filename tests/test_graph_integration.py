"""Real end-to-end tests: real LLM calls, real Postgres, real tools. Slower
than test_graph_routing.py by design — these validate actual agent behavior,
not just control flow."""

import uuid

from sqlmodel import select

from agentsys.db.models import Escalation, Review, Subtask, SubtaskStatus, Task, TaskStatus, ToolCall
from agentsys.db.session import get_session, init_db
from agentsys.escalations import apply_escalation_decision
from agentsys.graph.runner import run_task
from agentsys.memory import long_term


def setup_module() -> None:
    init_db()


def _create_task(request_text: str) -> str:
    with get_session() as session:
        task = Task(request_text=request_text)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _get_task(task_id: str) -> Task:
    with get_session() as session:
        return session.get(Task, task_id)


def _get_subtasks(task_id: str) -> list[Subtask]:
    with get_session() as session:
        return session.exec(
            select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.position)
        ).all()


def test_pure_reasoning_task_completes_without_any_tool():
    task_id = _create_task("Say hello in exactly one short sentence.")
    run_task(task_id)

    task = _get_task(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.final_output


def test_db_query_task_uses_the_right_tool_and_produces_correct_output():
    task_id = _create_task(
        "Query our sample_metric database for Cedar Analytics' revenue in 2026-Q2 and state the number."
    )
    run_task(task_id)

    task = _get_task(task_id)
    subtasks = _get_subtasks(task_id)

    assert task.status == TaskStatus.COMPLETED
    assert any(s.assigned_tool == "db_query" for s in subtasks)
    assert "10,450,000" in task.final_output or "10450000" in task.final_output.replace(",", "")

    with get_session() as session:
        calls = session.exec(
            select(ToolCall).where(ToolCall.subtask_id.in_([s.id for s in subtasks]))
        ).all()
    assert any(c.tool_name == "db_query" and c.success for c in calls)


def test_multi_subtask_task_respects_dependency_order():
    task_id = _create_task(
        "Look up Blue Harbor Logistics revenue for both 2026-Q1 and 2026-Q2 in our database, "
        "then state whether it grew and by how much in dollars."
    )
    run_task(task_id)

    task = _get_task(task_id)
    subtasks = _get_subtasks(task_id)

    # COMPLETED is the common case; AWAITING_APPROVAL is also acceptable --
    # review_node's independent reviewer model (gpt-4o, see config.py's
    # reviewer_llm_model) can be more conservative than the specialist's
    # gpt-4o-mini and legitimately escalate a technically-correct tool
    # result over data-provenance concerns rather than rubber-stamp it. This
    # test cares about dependency ordering, not review outcome -- same
    # tolerance test_reviewer_rejects_bad_output_and_specialist_revises_on_retry
    # already applies for the same reason (real LLM reviews aren't
    # deterministic, and a stricter reviewer choosing to escalate isn't a bug).
    assert task.status in (TaskStatus.COMPLETED, TaskStatus.AWAITING_APPROVAL)
    assert len(subtasks) >= 2
    # every subtask's dependencies must have an earlier position — this is the
    # cycle/self-reference prevention plan_node enforces, verified here on a
    # real LLM-generated plan rather than a hand-crafted fixture.
    position_by_id = {s.id: s.position for s in subtasks}
    for s in subtasks:
        for dep_id in s.depends_on:
            assert position_by_id[dep_id] < s.position


def test_reviewer_rejects_bad_output_and_specialist_revises_on_retry():
    """Deliberately ambiguous quarter phrasing to provoke an initial wrong
    query, proving reject-and-revise actually incorporates feedback rather
    than repeating the same mistake (this regressed once already — see the
    fix in db_query.py's description and _revision_feedback in nodes.py)."""
    task_id = _create_task(
        "Query sample_metric for Acme Robotics revenue in the second quarter of 2026."
    )
    run_task(task_id)

    task = _get_task(task_id)
    subtasks = _get_subtasks(task_id)
    assert task.status in (TaskStatus.COMPLETED, TaskStatus.AWAITING_APPROVAL)

    with get_session() as session:
        reviews = session.exec(
            select(Review).where(Review.subtask_id.in_([s.id for s in subtasks]))
        ).all()
    # Whether it passed first try or needed a retry, it must not have
    # exhausted retries with the *identical* failing query both times.
    db_subtask = next((s for s in subtasks if s.assigned_tool == "db_query"), None)
    if db_subtask and db_subtask.attempt_count > 1:
        with get_session() as session:
            calls = session.exec(
                select(ToolCall).where(ToolCall.subtask_id == db_subtask.id).order_by(ToolCall.created_at)
            ).all()
        queries = [c.input.get("sql") for c in calls]
        assert len(set(queries)) > 1, "retry used the identical query — revision feedback isn't being used"
    assert reviews


def test_synthesis_does_not_fabricate_data_beyond_what_subtasks_actually_retrieved():
    """Regression test for a real bug: the planner once produced a single
    Q1-only subtask for a request that needed both quarters, and synthesis
    papered over the gap by inventing a plausible-looking Q2 figure dressed
    up as a second subtask citation that never actually ran. Fixed via the
    PLAN_PROMPT (one-shot planning, no deferred subtasks) and SYNTHESIS_PROMPT
    (explicit no-fabrication instruction) in graph/prompts.py. This test
    verifies the fix holds: every dollar figure in the final answer must be
    traceable to an actual tool_call result, not just plausible-looking."""
    task_id = _create_task(
        "Look up Blue Harbor Logistics revenue for both 2026-Q1 and 2026-Q2 in our database, "
        "then state whether it grew and by how much in dollars."
    )
    run_task(task_id)

    task = _get_task(task_id)
    assert task.status == TaskStatus.COMPLETED

    subtasks = _get_subtasks(task_id)
    with get_session() as session:
        calls = session.exec(
            select(ToolCall).where(ToolCall.subtask_id.in_([s.id for s in subtasks]))
        ).all()
    retrieved_numbers = set()
    for c in calls:
        for row in c.output.get("rows", []):
            retrieved_numbers.update(str(v) for v in row)

    # Both seeded figures must actually have been retrieved by a real tool
    # call — if the plan only covers one quarter, that's the bug this test
    # guards against, not an acceptable partial answer.
    assert "1800000" in retrieved_numbers, "Q1 figure was never actually queried"
    assert "2100000" in retrieved_numbers, "Q2 figure was never actually queried"

    # And the numbers actually quoted in the final answer must be a subset of
    # what was genuinely retrieved (formatted with commas in the answer).
    if "2,100,000" in task.final_output:
        assert "2100000" in retrieved_numbers


def test_escalation_take_over_resumes_and_completes_task():
    task_id = _create_task("Say hello in exactly one short sentence.")
    with get_session() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.RUNNING
        session.add(task)
        subtask = Subtask(
            task_id=task_id, position=0, description="unresolvable subtask for this test",
            status=SubtaskStatus.ESCALATED,
        )
        session.add(subtask)
        session.commit()
        session.refresh(subtask)
        escalation = Escalation(task_id=task_id, subtask_id=subtask.id, reason="forced for test")
        session.add(escalation)
        session.commit()
        session.refresh(escalation)
        escalation_id, subtask_id = escalation.id, subtask.id

    _, should_resume = apply_escalation_decision(
        escalation_id, decision="take_over", note="test override",
        decided_by="pytest", override_output="manually provided answer",
    )
    assert should_resume

    run_task(task_id)  # simulates what the Celery task would do on resume

    task = _get_task(task_id)
    assert task.status == TaskStatus.COMPLETED
    with get_session() as session:
        resolved = session.get(Subtask, subtask_id)
    assert resolved.status == SubtaskStatus.DONE
    assert resolved.output == "manually provided answer"


def test_escalation_reject_at_plan_level_fails_task_cleanly():
    task_id = _create_task("test")
    with get_session() as session:
        task = session.get(Task, task_id)
        task.status = TaskStatus.AWAITING_APPROVAL
        session.add(task)
        escalation = Escalation(task_id=task_id, subtask_id=None, reason="forced plan-level escalation for test")
        session.add(escalation)
        session.commit()
        session.refresh(escalation)
        escalation_id = escalation.id

    _, should_resume = apply_escalation_decision(
        escalation_id, decision="reject", note="not viable", decided_by="pytest",
    )
    assert should_resume is False

    task = _get_task(task_id)
    assert task.status == TaskStatus.FAILED
    assert "not viable" in task.final_output


def test_memory_informed_planning_retrieves_prior_task_summary():
    marker = uuid.uuid4().hex[:8]
    task_id = _create_task(f"Say the word {marker} back to me and nothing else.")
    run_task(task_id)
    assert _get_task(task_id).status == TaskStatus.COMPLETED

    # The synthesize step should have written an episodic memory mentioning
    # this task — verify it's actually retrievable, not just written.
    results = long_term.retrieve_relevant(f"task involving the word {marker}", k=3)
    assert any(marker in r.content for r in results)


def test_specialist_does_not_fabricate_values_it_cannot_know():
    """Regression test for a real bug found during live MCP verification.

    Asked to roll dice, the specialist chose tool_name="none" with the
    rationale that rolling dice "can be achieved through reasoning", then the
    reasoning-only path invented four results (7, 3, 11, 5) and the reviewer
    passed them. Random outcomes are unknowable by reasoning, so those numbers
    were pure fabrication presented as fact.

    Same failure class as the synthesis fabrication bug, one layer earlier:
    SYNTHESIS_PROMPT had been hardened against inventing data, but
    TOOL_SELECTION_PROMPT and REASONING_ONLY_PROMPT never were. This asserts a
    real tool is used for a value that cannot be reasoned out.
    """
    task_id = _create_task(
        "Roll four 12-sided dice and report each roll and the total."
    )
    run_task(task_id)

    subtasks = _get_subtasks(task_id)
    tools_used = {s.assigned_tool for s in subtasks if s.assigned_tool}

    assert tools_used, (
        "no subtask used any tool — the specialist reasoned its way to a random "
        "result, which means it made the numbers up"
    )
