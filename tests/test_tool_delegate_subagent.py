import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from sqlmodel import select

from agentsys.db.models import SampleMetric, SubAgentRun, Subtask, SubtaskStatus, Task
from agentsys.db.session import get_session, init_db
from agentsys.tools.delegate_subagent import DelegateSubagentTool
from conftest import get_test_owner_id

pytestmark = pytest.mark.usefixtures("_seeded_db")


@pytest.fixture(scope="module", autouse=True)
def _seeded_db():
    init_db()
    with get_session() as session:
        existing = session.exec(
            select(SampleMetric).where(SampleMetric.company == "Cedar Analytics")
        ).first()
        if not existing:
            session.add(SampleMetric(company="Cedar Analytics", quarter="2026-Q1", revenue_usd=9_600_000, headcount=40))
            session.add(SampleMetric(company="Cedar Analytics", quarter="2026-Q2", revenue_usd=10_450_000, headcount=44))
            session.commit()
    yield


def _make_task_and_subtask(description: str) -> tuple[str, str]:
    with get_session() as session:
        task = Task(request_text=description, owner_id=get_test_owner_id())
        session.add(task)
        session.commit()
        session.refresh(task)
        subtask = Subtask(
            task_id=task.id, position=0, description=description, status=SubtaskStatus.RUNNING
        )
        session.add(subtask)
        session.commit()
        session.refresh(subtask)
        return task.id, subtask.id


def test_subagent_makes_multiple_tool_calls_and_records_a_run():
    goal = (
        "Query sample_metric for Cedar Analytics' 2026-Q1 revenue. If it's above "
        "$5,000,000, also look up their 2026-Q2 revenue and state both figures; "
        "otherwise just state the Q1 figure."
    )
    task_id, subtask_id = _make_task_and_subtask(goal)

    tool = DelegateSubagentTool()
    result = tool.run(goal=goal, task_id=task_id, subtask_id=subtask_id, depth=1)

    assert result.success is True
    answer = result.output["answer"].replace(",", "")
    assert "9600000" in answer
    # Cedar Q1 ($9.6M) is well above the $5M threshold, so a correct
    # sub-agent must make a SECOND query for Q2 before finishing -- proving
    # it actually inspected the first result instead of answering blind
    # after one call, which is the entire point of this tool over a normal
    # single-tool-call subtask.
    assert len(result.output["steps"]) >= 2
    assert sum(1 for step in result.output["steps"] if "db_query" in step) >= 2

    with get_session() as session:
        runs = session.exec(select(SubAgentRun).where(SubAgentRun.subtask_id == subtask_id)).all()
    assert len(runs) == 1
    assert runs[0].status.value == "done"
    assert runs[0].depth == 1


def test_depth_limit_prevents_further_delegation():
    goal = "Say hello in one short sentence."
    task_id, subtask_id = _make_task_and_subtask(goal)

    tool = DelegateSubagentTool()
    result = tool.run(goal=goal, task_id=task_id, subtask_id=subtask_id, depth=1)

    assert result.success is True
    with get_session() as session:
        runs = session.exec(select(SubAgentRun).where(SubAgentRun.subtask_id == subtask_id)).all()
    # Only the top-level run should exist -- no depth=2 child was spawned,
    # because delegate_subagent is excluded from a depth-1 sub-agent's own
    # available tools (depth(1) >= max_delegation_depth(1) by default).
    assert len(runs) == 1
