from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass

from sqlmodel import select

from agentsys.db.models import Escalation, Subtask, Task, ToolCall
from agentsys.db.session import get_session
from agentsys.eval.golden_dataset import GoldenTask
from agentsys.eval.grounding import check_grounding
from agentsys.eval.metrics import escalation_correctness, judge_outcome, step_efficiency, tool_call_precision
from agentsys.graph.runner import run_task


@dataclass
class EvalCaseResult:
    case_id: str
    category: str
    request_text: str
    outcome_correctness: float
    outcome: str
    outcome_reasoning: str
    ungrounded_figures: list[str]
    tool_call_precision: float | None
    escalation_correct: bool
    did_escalate: bool
    step_efficiency: float | None
    steps_taken: int
    final_status: str
    final_output: str | None
    error: str | None = None


@dataclass
class EvalReport:
    cases: list[EvalCaseResult]
    aggregates: dict

    def to_dict(self) -> dict:
        return {"aggregates": self.aggregates, "cases": [asdict(c) for c in self.cases]}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _evaluate_case(case: GoldenTask, owner_id: str) -> EvalCaseResult:
    try:
        with get_session() as session:
            task = Task(request_text=case.request_text, owner_id=owner_id, is_eval=True)
            session.add(task)
            session.commit()
            session.refresh(task)
            task_id = task.id

        # Synchronous, in-process -- same function a human-approved
        # escalation resume calls (see graph/runner.py's docstring), used
        # directly here instead of going through Celery so a golden-set run
        # is deterministic to invoke and doesn't need a worker running.
        run_task(task_id)

        with get_session() as session:
            task = session.get(Task, task_id)
            final_status = task.status.value
            final_output = task.final_output
            subtasks = session.exec(select(Subtask).where(Subtask.task_id == task_id)).all()
            escalations = session.exec(select(Escalation).where(Escalation.task_id == task_id)).all()
            tool_calls = session.exec(
                select(ToolCall).where(ToolCall.subtask_id.in_([s.id for s in subtasks]))
            ).all() if subtasks else []

        tool_names = [s.assigned_tool for s in subtasks if s.assigned_tool]
        did_escalate = final_status == "awaiting_approval" or len(escalations) > 0
        # Deterministic first, judge second: the grounding check costs nothing
        # and gives the judge evidence rather than asking it to notice a
        # fabrication unaided.
        grounding = check_grounding(
            final_output, [c.output for c in tool_calls], [c.tool_name for c in tool_calls]
        )
        judgment = judge_outcome(case, final_status, final_output, grounding)

        return EvalCaseResult(
            case_id=case.id,
            category=case.category,
            request_text=case.request_text,
            outcome_correctness=judgment.correctness,
            outcome=judgment.outcome,
            outcome_reasoning=judgment.reasoning,
            ungrounded_figures=grounding.ungrounded if grounding.looks_fabricated else [],
            tool_call_precision=tool_call_precision(case, tool_names),
            escalation_correct=escalation_correctness(case, did_escalate),
            did_escalate=did_escalate,
            step_efficiency=step_efficiency(case, len(subtasks)),
            steps_taken=len(subtasks),
            final_status=final_status,
            final_output=final_output,
        )
    except Exception as exc:  # keep the batch alive if one case fails
        return EvalCaseResult(
            case_id=case.id,
            category=case.category,
            request_text=case.request_text,
            outcome_correctness=0.0,
            outcome="miss",
            outcome_reasoning="",
            ungrounded_figures=[],
            tool_call_precision=None,
            escalation_correct=False,
            did_escalate=False,
            step_efficiency=None,
            steps_taken=0,
            final_status="error",
            final_output=None,
            error=str(exc),
        )


def _aggregate(results: list[EvalCaseResult]) -> dict:
    precision_vals = [r.tool_call_precision for r in results if r.tool_call_precision is not None]
    efficiency_vals = [r.step_efficiency for r in results if r.step_efficiency is not None]

    by_category: dict[str, float] = {}
    for category in {r.category for r in results}:
        subset = [r.outcome_correctness for r in results if r.category == category]
        by_category[category] = round(_mean(subset), 3)

    outcomes = {key: sum(1 for r in results if r.outcome == key)
                for key in ("pass", "stale", "invented", "miss")}

    return {
        "n_cases": len(results),
        "errors": sum(1 for r in results if r.error),
        "outcomes": outcomes,
        # The headline number. A run can hold correctness steady while
        # trading honest failures for confident fabrications, and a single
        # average would show that as no change at all.
        "invented_rate": round(outcomes["invented"] / len(results), 3) if results else 0.0,
        "overall_outcome_correctness": round(_mean([r.outcome_correctness for r in results]), 3),
        "outcome_correctness_by_category": by_category,
        "tool_call_precision": round(_mean(precision_vals), 3) if precision_vals else None,
        "escalation_accuracy": round(_mean([1.0 if r.escalation_correct else 0.0 for r in results]), 3),
        "step_efficiency": round(_mean(efficiency_vals), 3) if efficiency_vals else None,
        "avg_steps_taken": round(_mean([float(r.steps_taken) for r in results]), 2),
    }


def run_agent_eval(cases: list[GoldenTask], *, owner_id: str, max_workers: int = 4) -> EvalReport:
    results: list[EvalCaseResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_evaluate_case, case, owner_id): case for case in cases}
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r.case_id)
    return EvalReport(cases=results, aggregates=_aggregate(results))
