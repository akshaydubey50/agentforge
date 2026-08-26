"""Regression tests for the approve/re-escalate loop (audit R1).

The defect: approving a budget escalation set the task RUNNING and re-enqueued
it, but nothing moved the window the budget is measured over. agent_step_node
recomputed the same over-budget count from the same unchanged rows and
immediately created another identical escalation. Approving was a no-op that
manufactured a fresh escalation every time -- forever.

The full lifecycle each test walks:
    hits budget -> escalation -> human approves -> resumes -> makes progress
                                                          -> and does NOT
                                                             recreate the
                                                             identical
                                                             escalation.

No LLM calls: structured_complete is replaced, so what is under test is the
guard arithmetic, not the model. Needs a real Postgres (like the rest of this
suite).
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from sqlmodel import select

from agentsys.config import settings
from agentsys.db.models import (
    Escalation,
    EscalationStatus,
    LlmCall,
    Subtask,
    SubtaskStatus,
    Task,
    TaskStatus,
)
from agentsys.db.session import get_session, init_db
from agentsys.escalations import apply_escalation_decision
from agentsys.graph import nodes
from agentsys.graph.schemas import NextStepDecision
from agentsys.memory import short_term
from conftest import get_test_owner_id


def setup_module() -> None:
    init_db()


@pytest.fixture(autouse=True)
def _no_model_calls(monkeypatch):
    """agent_step_node makes exactly one structured call once its guards pass.
    Replacing it means reaching that call IS the proof of forward progress --
    the guards let the step through."""
    def _decide(*_args, **_kwargs):
        return (
            NextStepDecision(
                next_action="finish",
                rationale="stub: guards allowed this step through",
            ),
            object(),
        )

    monkeypatch.setattr(nodes, "structured_complete", _decide)
    monkeypatch.setattr(nodes.cost, "record_llm_call", lambda *a, **k: None)


def _make_task() -> str:
    with get_session() as session:
        task = Task(request_text="budget regression", owner_id=get_test_owner_id())
        session.add(task)
        session.commit()
        return task.id


def _fill_steps(task_id: str, count: int) -> None:
    """Subtasks are DONE so they count toward the budget without also
    tripping the unproductive-streak guard."""
    with get_session() as session:
        for position in range(count):
            session.add(
                Subtask(
                    task_id=task_id,
                    position=position,
                    description=f"step {position}",
                    status=SubtaskStatus.DONE,
                    output="done",
                )
            )
        session.commit()


def _budget_escalations(task_id: str) -> list[Escalation]:
    with get_session() as session:
        return list(
            session.exec(
                select(Escalation).where(
                    Escalation.task_id == task_id,
                    Escalation.kind == nodes.BUDGET_ESCALATION_KIND,
                )
            ).all()
        )


def _approve(escalation_id: str) -> None:
    apply_escalation_decision(escalation_id, decision="approve", note="continue", decided_by="tester")


# --- step budget -----------------------------------------------------------


def test_step_budget_escalates_then_resumes_without_recreating_itself():
    task_id = _make_task()
    _fill_steps(task_id, settings.max_task_steps)

    # 1. hits the step budget
    result = nodes.agent_step_node({"task_id": task_id})
    assert result["route"] == "escalate"

    escalations = _budget_escalations(task_id)
    assert len(escalations) == 1, "should have escalated exactly once"

    # 2. a human approves
    _approve(escalations[0].id)

    with get_session() as session:
        assert session.get(Task, task_id).status == TaskStatus.RUNNING

    # 3. it resumes and makes progress instead of re-escalating
    result = nodes.agent_step_node({"task_id": task_id})

    assert result["route"] != "escalate", "approving must not resume straight back into the guard"
    assert len(_budget_escalations(task_id)) == 1, "must not manufacture a second identical escalation"


def test_budget_still_bites_again_after_a_fresh_allowance_is_spent():
    """The fix must not disable the guard -- only move its window. Spend the
    new allowance and it must escalate again."""
    task_id = _make_task()
    _fill_steps(task_id, settings.max_task_steps)

    nodes.agent_step_node({"task_id": task_id})
    _approve(_budget_escalations(task_id)[0].id)

    # Spend the whole fresh allowance: these are created AFTER the approval,
    # so they fall inside the new window.
    _fill_steps(task_id, settings.max_task_steps)

    result = nodes.agent_step_node({"task_id": task_id})

    assert result["route"] == "escalate"
    assert len(_budget_escalations(task_id)) == 2, "a genuinely re-exceeded budget must escalate again"


# --- unproductive streak ---------------------------------------------------


def test_unproductive_streak_escalates_then_resumes_without_recreating_itself():
    task_id = _make_task()
    _fill_steps(task_id, 1)
    short_term.set_value(task_id, nodes.UNPRODUCTIVE_FIELD, settings.max_unproductive_steps)

    result = nodes.agent_step_node({"task_id": task_id})
    assert result["route"] == "escalate"

    escalations = _budget_escalations(task_id)
    assert len(escalations) == 1

    _approve(escalations[0].id)

    # The streak lives in Redis, so unlike the step/cost budgets it cannot be
    # re-derived from decided_at -- apply_escalation_decision must clear it.
    assert nodes._unproductive_streak(task_id) == 0

    result = nodes.agent_step_node({"task_id": task_id})

    assert result["route"] != "escalate"
    assert len(_budget_escalations(task_id)) == 1


# --- cost ceiling ----------------------------------------------------------


def test_cost_ceiling_escalates_then_resumes_without_recreating_itself():
    """Lifetime spend was the subtlest instance of the same bug: the sum could
    never go back down, so approving could never clear the guard."""
    task_id = _make_task()
    _fill_steps(task_id, 1)
    with get_session() as session:
        session.add(
            LlmCall(
                task_id=task_id, purpose="agent_step", model="test",
                prompt_tokens=0, completion_tokens=0,
                cost_usd=settings.max_task_cost_usd + 1.0,
            )
        )
        session.commit()

    result = nodes.agent_step_node({"task_id": task_id})
    assert result["route"] == "escalate"

    escalations = _budget_escalations(task_id)
    assert len(escalations) == 1

    _approve(escalations[0].id)
    result = nodes.agent_step_node({"task_id": task_id})

    assert result["route"] != "escalate", "already-spent money must not block the approved continuation"
    assert len(_budget_escalations(task_id)) == 1


# --- the window helper itself ----------------------------------------------


def test_budget_window_ignores_work_from_before_the_approval():
    task_id = _make_task()
    _fill_steps(task_id, settings.max_task_steps)

    nodes.agent_step_node({"task_id": task_id})
    _approve(_budget_escalations(task_id)[0].id)

    with get_session() as session:
        task = session.get(Task, task_id)
        subtasks = session.exec(select(Subtask).where(Subtask.task_id == task_id)).all()

    window_start = nodes._budget_window_start(task_id, task)
    counted = sum(1 for s in subtasks if nodes._naive(s.created_at) >= window_start)

    assert counted == 0, "pre-approval steps must fall outside the new window"
    assert len(subtasks) == settings.max_task_steps, "but the rows themselves are untouched"


def test_route_entry_still_treats_an_approved_task_as_a_resume():
    """The reason _budget_window_start is separate from _turn_start. If the
    turn boundary itself moved, a resumed task would look like a brand new
    turn and get re-classified through triage -- which route_entry documents
    must never happen to a resume."""
    task_id = _make_task()
    _fill_steps(task_id, settings.max_task_steps)

    nodes.agent_step_node({"task_id": task_id})
    _approve(_budget_escalations(task_id)[0].id)

    assert nodes.route_entry({"task_id": task_id}) == "agent_step"


def test_naive_normalises_both_directions():
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    naive = datetime(2026, 1, 1)

    assert nodes._naive(aware) == naive
    assert nodes._naive(naive) == naive
    # The comparison that would otherwise raise TypeError inside the guard.
    assert nodes._naive(aware) >= nodes._naive(naive - timedelta(days=1))


def test_rejecting_a_budget_escalation_still_fails_the_task():
    """The fix touches the approve path only -- reject must be unchanged."""
    task_id = _make_task()
    _fill_steps(task_id, settings.max_task_steps)
    nodes.agent_step_node({"task_id": task_id})

    escalation_id = _budget_escalations(task_id)[0].id
    _, should_resume = apply_escalation_decision(
        escalation_id, decision="reject", note="no", decided_by="tester"
    )

    assert should_resume is False
    with get_session() as session:
        assert session.get(Task, task_id).status == TaskStatus.FAILED
        assert session.get(Escalation, escalation_id).status == EscalationStatus.REJECTED
