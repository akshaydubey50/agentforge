import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from agentsys import context, telemetry
from agentsys.eval import deepeval_runner
from agentsys.eval import gate
from agentsys.eval.quality_adapter import (
    check_expected_args,
    check_expected_tools,
    check_quality_case_deterministic,
    normalize_run_records,
)
from agentsys.eval.quality_dataset import QualityCase, load_quality_dataset
from agentsys.policy import PolicyDecisionType, decide
from agentsys.tools.file_io import FileIOTool


def _case(**overrides) -> QualityCase:
    base = {
        "id": "c1",
        "category": "normal.tool",
        "levels": ["task", "tool"],
        "input": "Create a draft",
        "expected_outcome": "Draft created for approval",
        "expected_tools": ["gmail_create_draft"],
        "metrics": ["tool_correctness"],
    }
    base.update(overrides)
    return QualityCase(**base)


def _run():
    now = datetime.now(timezone.utc)
    task = SimpleNamespace(
        id="task-1",
        request_text="Create a Gmail draft with sk-secret in private notes",
        final_output="Draft is ready. Authorization: Bearer secret-token",
        status="awaiting_approval",
    )
    spans = [
        SimpleNamespace(
            span_type="agent_step",
            name="decide",
            input={},
            output={"context": {"selected_context_tokens": 120, "selected_memory_count": 1}},
            status="ok",
            started_at=now,
        ),
        SimpleNamespace(
            span_type="tool_call",
            name="gmail_create_draft",
            input={"tool_name": "gmail_create_draft"},
            output={"tool_name": "gmail_create_draft"},
            status="ok",
            started_at=now + timedelta(seconds=1),
        ),
        SimpleNamespace(
            span_type="verification",
            name="verify_subtask",
            input={},
            output={"route": "pass", "method": "gmail_draft", "reason": "body sk-secret"},
            status="ok",
            started_at=now + timedelta(seconds=2),
        ),
    ]
    tool_calls = [
        SimpleNamespace(
            tool_name="gmail_create_draft",
            input={
                "to": "recruiter@example.com",
                "subject": "Follow up",
                "body": "private email body sk-secret",
            },
            output={"draft_id": "draft-1", "raw": "secret raw provider response"},
            success=True,
            created_at=now + timedelta(seconds=1),
        )
    ]
    llm_calls = [
        SimpleNamespace(
            purpose="agent_step",
            model="openai/gpt-4o-mini",
            prompt_tokens=10,
            completion_tokens=5,
            cached_tokens=2,
            cost_usd=0.001,
            created_at=now,
        )
    ]
    return normalize_run_records(
        task=task,
        spans=spans,
        tool_calls=tool_calls,
        llm_calls=llm_calls,
        case_id="c1",
    )


def test_agentforge_trace_converts_into_eval_case():
    run = _run()

    assert run.task_id == "task-1"
    assert run.case_id == "c1"
    assert run.final_status == "awaiting_approval"
    assert run.context_metadata["selected_context_tokens"] == 120


def test_tool_calls_final_answer_and_verification_are_mapped():
    run = _run()

    assert [tool.name for tool in run.tools_called] == ["gmail_create_draft"]
    assert run.tools_called[0].args["to"] == "recruiter@example.com"
    assert run.actual_output == "[omitted:sensitive]"
    assert run.verification["route"] == "pass"
    assert run.llm_calls[0].prompt_tokens == 10


def test_trajectory_ordering_is_preserved():
    run = _run()

    assert [event.operation for event in run.trajectory] == [
        "agent_step",
        "tool_call",
        "verification",
    ]


def test_sensitive_bodies_are_omitted_from_normalized_eval_case():
    run = _run()

    dumped = run.model_dump_json()
    assert "sk-secret" not in dumped
    assert "private email body" not in dumped
    assert "Authorization" not in dumped
    assert run.tools_called[0].args["body"] == "[omitted:sensitive]"


def test_deterministic_expected_tool_check():
    run = _run()

    assert check_expected_tools(run, _case()).passed
    verdict = check_expected_tools(run, _case(expected_tools=["web_search"]))
    assert not verdict.passed
    assert "missing expected tools" in verdict.detail


def test_deterministic_expected_args_check():
    run = _run()
    case = _case(
        expected_args=[
            {
                "tool": "gmail_create_draft",
                "arg": "to",
                "value": "recruiter@example.com",
                "match": "exact",
            },
            {
                "tool": "gmail_create_draft",
                "arg": "subject",
                "value": "follow",
                "match": "contains",
            },
        ]
    )

    assert check_expected_args(run, case).passed
    failed = check_expected_args(
        run,
        _case(
            expected_args=[
                {
                    "tool": "gmail_create_draft",
                    "arg": "to",
                    "value": "other@example.com",
                    "match": "exact",
                }
            ]
        ),
    )
    assert not failed.passed


def test_quality_case_deterministic_checks_cover_verification_and_escalation():
    run = _run()
    checks = check_quality_case_deterministic(
        run,
        _case(expected_verification_route="pass", should_escalate=True, max_steps=2),
    )

    assert all(check.passed for check in checks)


def test_deepeval_absence_does_not_break_deterministic_quality_report(monkeypatch):
    run = _run()
    case = _case()
    monkeypatch.setattr(
        deepeval_runner,
        "_load_deepeval_symbols",
        lambda: (_ for _ in ()).throw(deepeval_runner.DeepEvalUnavailable("missing deepeval")),
    )

    report = deepeval_runner.run_quality_evaluation([run], [case], with_deepeval=True)

    assert report.status == "skipped"
    assert report.aggregates["deterministic_failed"] == 0
    assert "missing deepeval" in report.detail


def test_judge_failure_is_reported_per_metric(monkeypatch):
    run = _run()
    case = _case(metrics=["answer_relevance"])
    monkeypatch.setattr(deepeval_runner, "_load_deepeval_symbols", lambda: {"LLMTestCase": object})
    monkeypatch.setattr(deepeval_runner, "to_deepeval_test_case", lambda *_args: object())
    monkeypatch.setattr(
        deepeval_runner,
        "_metric_for",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("judge unavailable")),
    )

    report = deepeval_runner.run_quality_evaluation([run], [case], with_deepeval=True)

    assert report.status == "fail"
    assert report.cases[0].metrics[0].error == "RuntimeError: judge unavailable"


def test_deepeval_score_cannot_override_deterministic_failure(monkeypatch):
    class PassingMetric:
        threshold = 0.7
        score = 1.0
        reason = "looks good"
        model = "fake"

        def measure(self, _test_case):
            return None

        def is_successful(self):
            return True

    run = _run()
    case = _case(expected_tools=["web_search"], metrics=["answer_relevance"])
    monkeypatch.setattr(deepeval_runner, "_load_deepeval_symbols", lambda: {"LLMTestCase": object})
    monkeypatch.setattr(deepeval_runner, "to_deepeval_test_case", lambda *_args: object())
    monkeypatch.setattr(deepeval_runner, "_metric_for", lambda *_args, **_kwargs: PassingMetric())

    report = deepeval_runner.run_quality_evaluation([run], [case], with_deepeval=True)

    assert report.status == "fail"
    assert report.aggregates["deterministic_failed"] == 1
    assert report.cases[0].metrics[0].score == 1.0


def test_rag_metrics_are_visible_skips_without_evidence(monkeypatch):
    run = _run()
    case = _case(expected_tools=["gmail_create_draft"], metrics=["faithfulness"])
    monkeypatch.setattr(deepeval_runner, "_load_deepeval_symbols", lambda: {"LLMTestCase": object})
    monkeypatch.setattr(deepeval_runner, "to_deepeval_test_case", lambda *_args: object())
    monkeypatch.setattr(
        deepeval_runner,
        "_metric_for",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not build metric")),
    )

    report = deepeval_runner.run_quality_evaluation([run], [case], with_deepeval=True)

    assert report.status == "pass"
    assert report.cases[0].metrics[0].passed is None
    assert report.cases[0].metrics[0].reason == "skipped: no retrieval context or reference evidence"


def test_dataset_parser_validates_malformed_cases(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps([{"id": "missing-required-fields"}]), encoding="utf-8")

    with pytest.raises(Exception):
        load_quality_dataset(path)


def test_dataset_parser_rejects_duplicate_case_ids(tmp_path):
    item = _case().model_dump()
    path = tmp_path / "dupe.json"
    path.write_text(json.dumps([item, item]), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate quality case ids"):
        load_quality_dataset(path)


def test_shipped_quality_dataset_is_small_and_parseable():
    cases = load_quality_dataset()

    assert 1 <= len(cases) <= 12
    assert all(case.metrics for case in cases)
    assert all(not case.input.lower().startswith("real gmail") for case in cases)


def test_probabilistic_suite_can_be_skipped_without_network():
    report = deepeval_runner.run_quality_evaluation([_run()], [_case()], with_deepeval=False)

    assert report.status == "skipped"
    assert report.aggregates["deepeval_enabled"] is False


def test_quality_tier_applies_subset_and_sample_controls(monkeypatch):
    cases = [
        _case(id="normal-a", category="normal.web_research"),
        _case(id="failure-a", category="failure.recovery"),
        _case(id="normal-b", category="normal.rag_answer"),
    ]
    captured = {}

    def fake_run_quality_cases(cases_arg, **kwargs):
        captured["case_ids"] = [case.id for case in cases_arg]
        captured["max_cases"] = kwargs["max_cases"]
        return deepeval_runner.QualityReport(
            status="pass",
            detail="ok",
            cases=[],
            aggregates={"deepeval_avg_score": 0.8},
        )

    monkeypatch.setattr(gate.settings, "deepeval_dataset_subset", "normal")
    monkeypatch.setattr(gate.settings, "deepeval_sample_count", 1)
    monkeypatch.setattr(gate.settings, "deepeval_judge_model", "fake-judge")
    monkeypatch.setattr(gate.settings, "reviewer_llm_model", "fallback-judge")
    monkeypatch.setattr("agentsys.eval.deepeval_runner.deepeval_available", lambda: True)
    monkeypatch.setattr("agentsys.eval.quality_dataset.load_quality_dataset", lambda: cases)
    monkeypatch.setattr("agentsys.eval.quality_runner.run_quality_cases", fake_run_quality_cases)

    result = gate.run_deepeval_quality_tier("owner-1")

    assert result.status == "pass"
    assert captured == {"case_ids": ["normal-a", "normal-b"], "max_cases": 1}


def test_phase7b_telemetry_regression_for_safe_metadata():
    attrs = telemetry.span_attributes(
        span_id="s",
        task_id="t",
        subtask_id=None,
        span_type="tool_call",
        name="gmail_create_draft",
        status="ok",
        duration_ms=1,
        input={"body": "private body sk-secret", "tool_name": "gmail_create_draft"},
        output={"context": {"selected_context_tokens": 10}},
    )

    assert attrs["agentsys.tool.name"] == "gmail_create_draft"
    assert "sk-secret" not in " ".join(str(v) for v in attrs.values())


def test_phase6_context_memory_regression_stays_deterministic():
    results = context.run_representative_context_benchmark()

    assert results
    assert all(row["critical_evidence_preserved"] for row in results)


def test_phase1_to_5_action_safety_regression_still_blocks_write():
    decision = decide(FileIOTool(), {"action": "write", "path": "notes.txt", "content": "x"})

    assert decision.decision is PolicyDecisionType.REQUIRE_APPROVAL
