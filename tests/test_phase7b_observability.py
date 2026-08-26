from contextlib import contextmanager
from types import SimpleNamespace

from sqlmodel import select

from agentsys import cost, otel, telemetry
from agentsys.db.models import LlmCall, Task, TraceSpan
from agentsys.db.session import get_session, init_db
from agentsys.graph import tracing
from tests.conftest import get_test_owner_id


def _secret_blob() -> str:
    return (
        "Authorization: Bearer ya29.secret-oauth-token "
        "refresh_token=refresh-token-value sk-testsecret1234567890 "
        "candidate email body SECRET MEMORY BODY raw prompt"
    )


def test_span_attributes_export_safe_metadata_without_sensitive_bodies():
    attrs = telemetry.span_attributes(
        span_id="span-1",
        task_id="task-1",
        subtask_id="subtask-1",
        span_type="tool_call",
        name="gmail_create_draft",
        status="error",
        duration_ms=42,
        input={
            "to": "person@example.com",
            "body": "gmail body " + _secret_blob(),
            "Authorization": "Bearer should-not-export",
            "refresh_token": "refresh-should-not-export",
        },
        output={
            "success": False,
            "error": "provider failed with " + _secret_blob(),
            "execution": {
                "attempts": 2,
                "safety": "non_retryable_side_effect",
                "failure_kind": "transient",
                "retry_reason": "timeout",
                "deduped": False,
                "refused_ambiguous": True,
            },
            "context": {
                "usable_input_budget_tokens": 22000,
                "selected_context_tokens": 18000,
                "tokens_avoided": 4000,
                "compression_ratio": 0.72,
                "conversation_tokens": 500,
                "summary_tokens": 120,
                "retrieved_memory_count": 7,
                "selected_memory_count": 3,
                "dropped_memory_count": 4,
                "selected_memory_tokens": 640,
                "artifact_references": 2,
            },
        },
    )

    joined = " ".join(str(value) for value in attrs.values())
    assert attrs["agentsys.tool.name"] == "gmail_create_draft"
    assert attrs["agentsys.tool.attempt_count"] == 2
    assert attrs["agentsys.tool.execution_safety"] == "non_retryable_side_effect"
    assert attrs["agentsys.tool.refused_ambiguous"] is True
    assert attrs["agentsys.context.usable_budget_tokens"] == 22000
    assert attrs["agentsys.context.selected_tokens"] == 18000
    assert attrs["agentsys.context.tokens_avoided"] == 4000
    assert attrs["agentsys.memory.retrieved_count"] == 7
    assert attrs["agentsys.memory.selected_count"] == 3
    assert attrs["agentsys.context.artifact_reference_count"] == 2
    assert "Authorization" not in joined
    assert "should-not-export" not in joined
    assert "refresh-token-value" not in joined
    assert "gmail body" not in joined
    assert "SECRET MEMORY BODY" not in joined
    assert "raw prompt" not in joined


def test_exception_sanitization_exports_category_not_provider_message():
    attrs = telemetry.safe_error_attributes(
        "401 request failed with Authorization: Bearer ya29.private-token and email body"
    )

    assert attrs == {
        "agentsys.error.present": True,
        "agentsys.error.category": "auth_error",
    }
    assert "private-token" not in " ".join(str(value) for value in attrs.values())


def test_llm_attributes_export_tokens_and_cost_without_prompt_or_response():
    attrs = telemetry.llm_attributes(
        task_id="task-1",
        subtask_id=None,
        llm_call_id="llm-1",
        purpose="agent_step",
        model="openai/gpt-4o-mini",
        prompt_tokens=123,
        completion_tokens=45,
        cached_tokens=20,
        cost_usd=0.001234567,
        provider="openai",
    )

    joined_keys = " ".join(attrs.keys())
    joined_values = " ".join(str(value) for value in attrs.values())
    assert attrs["agentsys.llm.provider"] == "openai"
    assert attrs["agentsys.llm_call_id"] == "llm-1"
    assert attrs["agentsys.llm.model"] == "openai/gpt-4o-mini"
    assert attrs["agentsys.llm.input_tokens"] == 123
    assert attrs["agentsys.llm.output_tokens"] == 45
    assert attrs["agentsys.llm.cached_tokens"] == 20
    assert attrs["agentsys.llm.cost_usd"] == 0.00123457
    assert "prompt" not in joined_keys
    assert "response" not in joined_keys
    assert "SECRET" not in joined_values


def test_policy_and_verification_results_are_observable_as_safe_metadata():
    policy_attrs = telemetry.span_attributes(
        span_id="span-policy",
        task_id="task-1",
        subtask_id="subtask-1",
        span_type="escalation",
        name="escalation_created",
        status="ok",
        duration_ms=1,
        input={
            "kind": "tool_approval",
            "tool_name": "gmail_create_draft",
            "policy_decision": "require_approval",
            "action_type": "external_write",
            "risk": "medium",
            "reason": "contains a private draft body that must not export",
        },
        output={"reason": "private approval reason"},
    )
    verification_attrs = telemetry.span_attributes(
        span_id="span-verification",
        task_id="task-1",
        subtask_id="subtask-1",
        span_type="verification",
        name="verify_subtask",
        status="error",
        duration_ms=5,
        input={"tool_success": True, "success_criteria": "private criteria"},
        output={
            "route": "retry",
            "method": "gmail_draft",
            "retryable": True,
            "needs_replan": False,
            "needs_human": False,
            "reason": "private verifier reason",
        },
    )

    assert policy_attrs["agentsys.policy.decision"] == "require_approval"
    assert policy_attrs["agentsys.policy.action_type"] == "external_write"
    assert policy_attrs["agentsys.policy.risk"] == "medium"
    assert verification_attrs["agentsys.verification.route"] == "retry"
    assert verification_attrs["agentsys.verification.method"] == "gmail_draft"
    assert verification_attrs["agentsys.verification.retryable"] is True
    assert "private draft body" not in " ".join(str(value) for value in policy_attrs.values())
    assert "private verifier reason" not in " ".join(str(value) for value in verification_attrs.values())


def test_disabled_otel_produces_no_external_export(monkeypatch):
    monkeypatch.setattr(otel.settings, "otel_exporter_otlp_endpoint", "")
    monkeypatch.setattr(otel, "_tracer", None)
    monkeypatch.setattr(otel, "_init_attempted", False)

    with otel.span("off", span_type="agent", task_id="task-1") as span:
        assert span is None


def test_tracing_persistence_and_events_survive_telemetry_export_failure(monkeypatch):
    init_db()
    owner_id = get_test_owner_id()
    with get_session() as session:
        task = Task(owner_id=owner_id, request_text="test observability")
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    published = []
    monkeypatch.setattr(tracing.events, "publish", lambda *args: published.append(args))

    class BrokenSpan:
        def set_attribute(self, *_args, **_kwargs):
            raise RuntimeError("export failed with " + _secret_blob())

    @contextmanager
    def fake_otel_span(*_args, **_kwargs):
        yield BrokenSpan()

    monkeypatch.setattr(tracing.otel, "span", fake_otel_span)

    with tracing.span(
        task_id,
        "agent_step",
        "decide_step_1",
        input={"Authorization": "Bearer not-for-telemetry"},
    ) as box:
        box["output"] = {
            "context": {
                "selected_context_tokens": 100,
                "tokens_avoided": 25,
                "selected_memory_count": 1,
            }
        }

    with get_session() as session:
        row = session.exec(select(TraceSpan).where(TraceSpan.task_id == task_id)).one()

    assert row.status == "ok"
    assert row.output["context"]["selected_context_tokens"] == 100
    assert [event[1] for event in published] == ["span_start", "span_end"]


def test_tracing_persistence_survives_telemetry_attribute_builder_failure(monkeypatch):
    init_db()
    owner_id = get_test_owner_id()
    with get_session() as session:
        task = Task(owner_id=owner_id, request_text="test telemetry builder failure")
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    published = []
    monkeypatch.setattr(tracing.events, "publish", lambda *args: published.append(args))
    monkeypatch.setattr(tracing, "span_attributes", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError(_secret_blob())))

    with tracing.span(task_id, "agent_step", "decide_step_1") as box:
        box["output"] = {"safe": True}

    with get_session() as session:
        row = session.exec(select(TraceSpan).where(TraceSpan.task_id == task_id)).one()

    assert row.status == "ok"
    assert row.output == {"safe": True}
    assert [event[1] for event in published] == ["span_start", "span_end"]


def test_record_llm_call_persists_truth_when_external_export_fails(monkeypatch):
    init_db()
    owner_id = get_test_owner_id()
    with get_session() as session:
        task = Task(owner_id=owner_id, request_text="test llm observability")
        session.add(task)
        session.commit()
        session.refresh(task)
        task_id = task.id

    captured = {}

    def fail_export(**kwargs):
        captured.update(kwargs)
        raise RuntimeError("collector failed with " + _secret_blob())

    monkeypatch.setattr(cost.otel, "record_llm_usage", fail_export)
    completion = SimpleNamespace(
        model="openai/gpt-4o-mini",
        usage=SimpleNamespace(
            prompt_tokens=11,
            completion_tokens=7,
            prompt_tokens_details=SimpleNamespace(cached_tokens=3),
        ),
    )

    cost.record_llm_call(task_id, None, "agent_step", completion)

    with get_session() as session:
        row = session.exec(select(LlmCall).where(LlmCall.task_id == task_id)).one()
    assert row.model == "openai/gpt-4o-mini"
    assert row.prompt_tokens == 11
    assert row.completion_tokens == 7
    assert row.cached_tokens == 3
    assert captured["llm_call_id"] == row.id
