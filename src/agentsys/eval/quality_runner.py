from __future__ import annotations

from agentsys.db.models import Task
from agentsys.db.session import get_session
from agentsys.eval.deepeval_runner import QualityReport, run_quality_evaluation, write_quality_report
from agentsys.eval.quality_adapter import AgentRunEvalInput, normalize_task_id
from agentsys.eval.quality_dataset import QualityCase
from agentsys.graph.runner import run_task


def run_quality_cases(
    cases: list[QualityCase],
    *,
    owner_id: str,
    with_deepeval: bool = False,
    judge_model: str | None = None,
    max_cases: int | None = None,
) -> QualityReport:
    selected = cases[:max_cases] if max_cases is not None else cases
    runs: list[AgentRunEvalInput] = []

    for case in selected:
        with get_session() as session:
            task = Task(request_text=case.input, owner_id=owner_id, is_eval=True)
            session.add(task)
            session.commit()
            session.refresh(task)
            task_id = task.id

        try:
            run_task(task_id)
        finally:
            runs.append(normalize_task_id(task_id, case_id=case.id))

    report = run_quality_evaluation(
        runs,
        selected,
        with_deepeval=with_deepeval,
        judge_model=judge_model,
    )
    write_quality_report(report)
    return report
