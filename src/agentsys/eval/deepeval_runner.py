from __future__ import annotations

import importlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentsys.config import PROJECT_ROOT, settings
from agentsys.eval.quality_adapter import (
    AgentRunEvalInput,
    DeterministicCheck,
    check_quality_case_deterministic,
)
from agentsys.eval.quality_dataset import QualityCase


class DeepEvalUnavailable(RuntimeError):
    pass


@dataclass
class QualityMetricResult:
    case_id: str
    metric: str
    score: float | None
    threshold: float | None
    passed: bool | None
    reason: str = ""
    model: str | None = None
    latency_ms: int | None = None
    error: str | None = None


@dataclass
class QualityCaseResult:
    case_id: str
    category: str
    deterministic: list[dict]
    metrics: list[QualityMetricResult] = field(default_factory=list)

    @property
    def deterministic_passed(self) -> bool:
        return all(check["passed"] for check in self.deterministic if check["blocking"])


@dataclass
class QualityReport:
    status: str
    detail: str
    cases: list[QualityCaseResult]
    aggregates: dict

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "detail": self.detail,
            "aggregates": self.aggregates,
            "cases": [
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "deterministic": case.deterministic,
                    "metrics": [asdict(metric) for metric in case.metrics],
                }
                for case in self.cases
            ],
        }


def deepeval_available() -> bool:
    return importlib.util.find_spec("deepeval") is not None


def _load_deepeval_symbols() -> dict[str, Any]:
    if not deepeval_available():
        raise DeepEvalUnavailable("deepeval is not installed; install requirements-eval.txt")
    test_case = importlib.import_module("deepeval.test_case")
    metrics = importlib.import_module("deepeval.metrics")
    return {
        "LLMTestCase": getattr(test_case, "LLMTestCase"),
        "ToolCall": getattr(test_case, "ToolCall"),
        "AnswerRelevancyMetric": getattr(metrics, "AnswerRelevancyMetric"),
        "ToolCorrectnessMetric": getattr(metrics, "ToolCorrectnessMetric"),
        "ArgumentCorrectnessMetric": getattr(metrics, "ArgumentCorrectnessMetric"),
        "FaithfulnessMetric": getattr(metrics, "FaithfulnessMetric"),
        "ContextualRelevancyMetric": getattr(metrics, "ContextualRelevancyMetric"),
        "GEval": getattr(metrics, "GEval", None),
        "LLMTestCaseParams": getattr(test_case, "LLMTestCaseParams", None),
    }


def _tool_call(symbols: dict[str, Any], *, name: str, args: dict, output: dict | None = None):
    cls = symbols["ToolCall"]
    try:
        return cls(name=name, input=args, output=output)
    except TypeError:
        try:
            return cls(name=name, input_parameters=args, output=output)
        except TypeError:
            return cls(name=name)


def to_deepeval_test_case(run: AgentRunEvalInput, case: QualityCase):
    symbols = _load_deepeval_symbols()
    LLMTestCase = symbols["LLMTestCase"]
    tools_called = [
        _tool_call(symbols, name=tool.name, args=tool.args, output=tool.output)
        for tool in run.tools_called
    ]
    expected_tools = [
        _tool_call(symbols, name=name, args={}, output=None) for name in case.expected_tools
    ]

    kwargs = {
        "input": run.input,
        "actual_output": run.actual_output,
        "expected_output": case.expected_outcome,
        "tools_called": tools_called,
        "expected_tools": expected_tools,
    }
    if run.rag_context or case.reference_evidence:
        kwargs["retrieval_context"] = run.rag_context or case.reference_evidence
    return LLMTestCase(**kwargs)


def _metric_for(name: str, symbols: dict[str, Any], *, model: str | None, threshold: float | None):
    options = {"threshold": threshold, "include_reason": True}
    if model:
        options["model"] = model

    if name == "answer_relevance":
        return symbols["AnswerRelevancyMetric"](**options)
    if name == "tool_correctness":
        return symbols["ToolCorrectnessMetric"](threshold=threshold, include_reason=True)
    if name == "argument_correctness":
        return symbols["ArgumentCorrectnessMetric"](**options)
    if name == "faithfulness":
        return symbols["FaithfulnessMetric"](**options)
    if name == "contextual_relevance":
        return symbols["ContextualRelevancyMetric"](**options)
    if name == "task_completion":
        GEval = symbols.get("GEval")
        params = symbols.get("LLMTestCaseParams")
        if GEval is None or params is None:
            raise DeepEvalUnavailable("deepeval GEval is unavailable for task_completion")
        geval_options = {
            "name": "Task Completion",
            "criteria": (
                "Score whether the actual output completed the user's task and is "
                "consistent with the expected outcome. Penalize unsupported claims."
            ),
            "evaluation_params": [params.INPUT, params.ACTUAL_OUTPUT, params.EXPECTED_OUTPUT],
            "threshold": threshold,
        }
        if model:
            geval_options["model"] = model
        return GEval(**geval_options)
    raise ValueError(f"unsupported DeepEval metric: {name}")


def _measure_metric(metric: Any, test_case: Any) -> QualityMetricResult:
    start = time.time()
    try:
        metric.measure(test_case)
        latency_ms = int((time.time() - start) * 1000)
        success = getattr(metric, "is_successful", None)
        passed = success() if callable(success) else success
        return QualityMetricResult(
            case_id="",
            metric=metric.__class__.__name__,
            score=getattr(metric, "score", None),
            threshold=getattr(metric, "threshold", None),
            passed=passed,
            reason=str(getattr(metric, "reason", "") or ""),
            model=str(getattr(metric, "model", "") or "") or None,
            latency_ms=latency_ms,
        )
    except Exception as exc:
        latency_ms = int((time.time() - start) * 1000)
        return QualityMetricResult(
            case_id="",
            metric=metric.__class__.__name__,
            score=None,
            threshold=getattr(metric, "threshold", None),
            passed=None,
            latency_ms=latency_ms,
            error=f"{type(exc).__name__}: {exc}",
        )


def _metric_skip_reason(metric_name: str, run: AgentRunEvalInput, case: QualityCase) -> str | None:
    if metric_name in {"faithfulness", "contextual_relevance"} and not (
        run.rag_context or case.reference_evidence
    ):
        return "skipped: no retrieval context or reference evidence"
    return None


def run_quality_evaluation(
    runs: list[AgentRunEvalInput],
    cases: list[QualityCase],
    *,
    with_deepeval: bool = False,
    judge_model: str | None = None,
    threshold: float = 0.7,
) -> QualityReport:
    cases_by_id = {case.id: case for case in cases}
    results: list[QualityCaseResult] = []
    unavailable = None
    symbols = None

    if with_deepeval:
        try:
            symbols = _load_deepeval_symbols()
        except DeepEvalUnavailable as exc:
            unavailable = str(exc)

    for run in runs:
        if run.case_id is None or run.case_id not in cases_by_id:
            raise ValueError(f"run has no matching quality case: {run.case_id}")
        case = cases_by_id[run.case_id]
        deterministic_checks: list[DeterministicCheck] = check_quality_case_deterministic(run, case)
        case_result = QualityCaseResult(
            case_id=case.id,
            category=case.category,
            deterministic=[check.to_dict() for check in deterministic_checks],
        )

        if with_deepeval and symbols is not None:
            test_case = to_deepeval_test_case(run, case)
            for metric_name in case.metrics:
                if metric_name == "step_efficiency":
                    continue
                if skip_reason := _metric_skip_reason(metric_name, run, case):
                    case_result.metrics.append(
                        QualityMetricResult(
                            case_id=case.id,
                            metric=metric_name,
                            score=None,
                            threshold=threshold,
                            passed=None,
                            reason=skip_reason,
                        )
                    )
                    continue
                try:
                    metric = _metric_for(
                        metric_name,
                        symbols,
                        model=judge_model,
                        threshold=threshold,
                    )
                    measured = _measure_metric(metric, test_case)
                    measured.case_id = case.id
                    measured.metric = metric_name
                    case_result.metrics.append(measured)
                except Exception as exc:
                    case_result.metrics.append(
                        QualityMetricResult(
                            case_id=case.id,
                            metric=metric_name,
                            score=None,
                            threshold=threshold,
                            passed=None,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )

        results.append(case_result)

    deterministic_failed = sum(1 for result in results if not result.deterministic_passed)
    metric_values = [
        metric.score
        for result in results
        for metric in result.metrics
        if metric.score is not None
    ]
    metric_failures = [
        metric
        for result in results
        for metric in result.metrics
        if metric.passed is False or metric.error
    ]
    status = "pass"
    detail = "ok"
    if deterministic_failed:
        status = "fail"
        detail = f"{deterministic_failed} deterministic quality case(s) failed"
    elif with_deepeval and unavailable:
        status = "skipped"
        detail = unavailable
    elif with_deepeval and metric_failures:
        status = "fail"
        detail = f"{len(metric_failures)} DeepEval metric(s) failed or errored"
    elif not with_deepeval:
        status = "skipped"
        detail = "DeepEval disabled; deterministic quality checks only"

    return QualityReport(
        status=status,
        detail=detail,
        cases=results,
        aggregates={
            "n_cases": len(results),
            "deterministic_failed": deterministic_failed,
            "deepeval_enabled": with_deepeval,
            "deepeval_unavailable": unavailable,
            "deepeval_metric_count": sum(len(result.metrics) for result in results),
            "deepeval_avg_score": round(sum(metric_values) / len(metric_values), 3)
            if metric_values
            else None,
            "judge_model": judge_model,
        },
    )


def write_quality_report(report: QualityReport) -> Path:
    out_dir = PROJECT_ROOT / "data" / "eval" / "deepeval_quality"
    out_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": settings.llm_model,
        "reviewer_model": settings.reviewer_llm_model,
        **report.to_dict(),
    }
    latest = out_dir / "latest.json"
    latest.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    with (out_dir / "runs.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({k: v for k, v in entry.items() if k != "cases"}) + "\n")
    return latest
