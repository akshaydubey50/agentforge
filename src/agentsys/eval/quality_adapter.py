from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import select

from agentsys.db.models import LlmCall, Subtask, Task, ToolCall, TraceSpan
from agentsys.db.session import get_session
from agentsys.eval.quality_dataset import ExpectedArg, QualityCase

_SENSITIVE_KEY_PARTS = (
    "authorization",
    "body",
    "content",
    "cookie",
    "credential",
    "oauth",
    "password",
    "prompt",
    "raw",
    "refresh",
    "response",
    "secret",
    "token",
)
_SENSITIVE_VALUE_PARTS = ("bearer ", "sk-", "secret", "refresh_token", "access_token")

_SAFE_CONTEXT_KEYS = {
    "usable_input_budget_tokens",
    "selected_context_tokens",
    "selected_pre_compression_tokens",
    "selected_post_compression_tokens",
    "compression_ratio",
    "tokens_avoided",
    "retrieved_memory_count",
    "selected_memory_count",
    "dropped_memory_count",
    "selected_memory_tokens",
    "selected_memory_kinds",
    "compressed_block_count",
    "compression_failure_count",
    "artifact_references",
    "capacity_error",
}


class NormalizedToolCall(BaseModel):
    name: str
    args: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    success: bool | None = None


class NormalizedTrajectoryEvent(BaseModel):
    actor: str
    operation: str
    tool: str | None = None
    decision: str | None = None
    result: str | None = None


class NormalizedLlmCall(BaseModel):
    purpose: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int | None = None
    cost_usd: float | None = None


class AgentRunEvalInput(BaseModel):
    case_id: str | None = None
    task_id: str
    input: str
    actual_output: str
    final_status: str
    tools_called: list[NormalizedToolCall] = Field(default_factory=list)
    trajectory: list[NormalizedTrajectoryEvent] = Field(default_factory=list)
    verification: dict[str, Any] = Field(default_factory=dict)
    rag_context: list[str] = Field(default_factory=list)
    context_metadata: dict[str, Any] = Field(default_factory=dict)
    llm_calls: list[NormalizedLlmCall] = Field(default_factory=list)


@dataclass
class DeterministicCheck:
    name: str
    passed: bool
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "blocking": self.blocking,
        }


def _status(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _sort_key(row: Any, attr: str) -> datetime:
    return getattr(row, attr, None) or datetime.min.replace(tzinfo=timezone.utc)


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(part in lower for part in _SENSITIVE_KEY_PARTS)


def _looks_sensitive_value(value: str) -> bool:
    lower = value.lower()
    return any(part in lower for part in _SENSITIVE_VALUE_PARTS)


def sanitize_eval_value(value: Any, *, key: str = "") -> Any:
    """Return a JSON-safe, eval-safe copy without private text bodies.

    This is deliberately stricter than runtime storage. Eval records need
    enough structure for quality scoring, not full Gmail, Drive, memory, prompt,
    response, credential, or raw tool-output bodies.
    """
    if key and _is_sensitive_key(key):
        return "[omitted:sensitive]"
    if isinstance(value, dict):
        return {str(k): sanitize_eval_value(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_eval_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_eval_value(item) for item in value]
    if isinstance(value, str):
        if _looks_sensitive_value(value):
            return "[omitted:sensitive]"
        return value[:500]
    return value


def _tool_call_from_record(row: Any) -> NormalizedToolCall:
    return NormalizedToolCall(
        name=str(getattr(row, "tool_name")),
        args=sanitize_eval_value(getattr(row, "input", {}) or {}),
        output=sanitize_eval_value(getattr(row, "output", {}) or {}),
        success=getattr(row, "success", None),
    )


def _span_event(row: Any) -> NormalizedTrajectoryEvent:
    span_type = str(getattr(row, "span_type", "span"))
    name = str(getattr(row, "name", span_type))
    output = getattr(row, "output", {}) or {}
    input_ = getattr(row, "input", {}) or {}

    actor = "system"
    if span_type in {"agent", "agent_step", "triage", "quick_reply", "sketch", "tool_selection", "reasoning", "review", "synthesize", "plan"}:
        actor = "agent"
    elif span_type == "tool_call":
        actor = "tool"

    tool = None
    if span_type == "tool_call":
        tool = str(output.get("tool_name") or input_.get("tool_name") or name)

    decision = (
        output.get("route")
        or output.get("decision")
        or input_.get("policy_decision")
        or input_.get("decision")
    )
    result = "ok" if _status(getattr(row, "status", "")) == "ok" else "error"
    if span_type == "verification" and output.get("route"):
        result = str(output.get("route"))

    return NormalizedTrajectoryEvent(
        actor=actor,
        operation=span_type,
        tool=tool,
        decision=str(decision) if decision is not None else None,
        result=result,
    )


def _verification_from_spans(spans: list[Any]) -> dict[str, Any]:
    verification_spans = [span for span in spans if getattr(span, "span_type", None) == "verification"]
    if not verification_spans:
        return {}
    latest = sorted(verification_spans, key=lambda row: _sort_key(row, "started_at"))[-1]
    return sanitize_eval_value(getattr(latest, "output", {}) or {})


def _context_metadata_from_spans(spans: list[Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for span in spans:
        output = getattr(span, "output", {}) or {}
        context = output.get("context") if isinstance(output, dict) else None
        if not isinstance(context, dict):
            continue
        for key, value in context.items():
            if key in _SAFE_CONTEXT_KEYS:
                merged[key] = sanitize_eval_value(value)
    return merged


def _rag_context_from_spans(spans: list[Any]) -> list[str]:
    context: list[str] = []
    for span in spans:
        output = getattr(span, "output", {}) or {}
        if not isinstance(output, dict):
            continue
        for key in ("retrieval_context", "rag_context", "evidence"):
            value = output.get(key)
            if isinstance(value, list):
                context.extend(str(sanitize_eval_value(item))[:500] for item in value)
            elif isinstance(value, str):
                context.append(str(sanitize_eval_value(value))[:500])
    return context


def normalize_run_records(
    *,
    task: Any,
    spans: list[Any] | None = None,
    tool_calls: list[Any] | None = None,
    llm_calls: list[Any] | None = None,
    case_id: str | None = None,
) -> AgentRunEvalInput:
    spans = sorted(spans or [], key=lambda row: _sort_key(row, "started_at"))
    tool_calls = sorted(tool_calls or [], key=lambda row: _sort_key(row, "created_at"))
    llm_calls = sorted(llm_calls or [], key=lambda row: _sort_key(row, "created_at"))

    trajectory = [_span_event(span) for span in spans]
    if not trajectory:
        trajectory = [
            NormalizedTrajectoryEvent(
                actor="tool",
                operation="tool_call",
                tool=str(getattr(call, "tool_name")),
                result="ok" if getattr(call, "success", None) else "error",
            )
            for call in tool_calls
        ]

    return AgentRunEvalInput(
        case_id=case_id,
        task_id=str(getattr(task, "id")),
        input=str(sanitize_eval_value(getattr(task, "request_text"))),
        actual_output=str(sanitize_eval_value(getattr(task, "final_output") or "")),
        final_status=_status(getattr(task, "status", "")),
        tools_called=[_tool_call_from_record(call) for call in tool_calls],
        trajectory=trajectory,
        verification=_verification_from_spans(spans),
        rag_context=_rag_context_from_spans(spans),
        context_metadata=_context_metadata_from_spans(spans),
        llm_calls=[
            NormalizedLlmCall(
                purpose=str(getattr(call, "purpose")),
                model=str(getattr(call, "model")),
                prompt_tokens=int(getattr(call, "prompt_tokens")),
                completion_tokens=int(getattr(call, "completion_tokens")),
                cached_tokens=getattr(call, "cached_tokens", None),
                cost_usd=getattr(call, "cost_usd", None),
            )
            for call in llm_calls
        ],
    )


def normalize_task_id(task_id: str, *, case_id: str | None = None) -> AgentRunEvalInput:
    with get_session() as session:
        task = session.get(Task, task_id)
        if task is None:
            raise ValueError(f"task not found: {task_id}")
        subtasks = session.exec(select(Subtask).where(Subtask.task_id == task_id)).all()
        subtask_ids = [subtask.id for subtask in subtasks]
        tool_calls = (
            session.exec(
                select(ToolCall)
                .where(ToolCall.subtask_id.in_(subtask_ids))
                .order_by(ToolCall.created_at)
            ).all()
            if subtask_ids
            else []
        )
        spans = session.exec(
            select(TraceSpan).where(TraceSpan.task_id == task_id).order_by(TraceSpan.started_at)
        ).all()
        llm_calls = session.exec(
            select(LlmCall).where(LlmCall.task_id == task_id).order_by(LlmCall.created_at)
        ).all()

    return normalize_run_records(
        task=task,
        spans=list(spans),
        tool_calls=list(tool_calls),
        llm_calls=list(llm_calls),
        case_id=case_id,
    )


def check_expected_tools(run: AgentRunEvalInput, case: QualityCase) -> DeterministicCheck:
    called = [tool.name for tool in run.tools_called]
    missing = [tool for tool in case.expected_tools if tool not in called]
    forbidden = [tool for tool in case.forbidden_tools if tool in called]
    if missing:
        return DeterministicCheck("expected_tools", False, f"missing expected tools: {missing}")
    if forbidden:
        return DeterministicCheck("expected_tools", False, f"forbidden tools called: {forbidden}")
    return DeterministicCheck("expected_tools", True, "ok")


def _arg_matches(actual: Any, expected: ExpectedArg) -> bool:
    actual_text = str(actual)
    if expected.match == "exact":
        return actual_text == expected.value
    return expected.value.lower() in actual_text.lower()


def check_expected_args(run: AgentRunEvalInput, case: QualityCase) -> DeterministicCheck:
    for expected in case.expected_args:
        matching_calls = [tool for tool in run.tools_called if tool.name == expected.tool]
        if not matching_calls:
            return DeterministicCheck("expected_args", False, f"{expected.tool} was not called")
        if not any(_arg_matches(tool.args.get(expected.arg), expected) for tool in matching_calls):
            return DeterministicCheck(
                "expected_args",
                False,
                f"{expected.tool}.{expected.arg} did not match {expected.match}:{expected.value}",
            )
    return DeterministicCheck("expected_args", True, "ok")


def check_quality_case_deterministic(
    run: AgentRunEvalInput, case: QualityCase
) -> list[DeterministicCheck]:
    checks = [check_expected_tools(run, case), check_expected_args(run, case)]

    if case.expected_verification_route is not None:
        route = str(run.verification.get("route", ""))
        checks.append(
            DeterministicCheck(
                "verification_route",
                route == case.expected_verification_route,
                "ok" if route == case.expected_verification_route else f"got {route or 'none'}",
            )
        )

    if case.should_complete is not None:
        completed = run.final_status == "completed"
        checks.append(
            DeterministicCheck(
                "completion_state",
                completed == case.should_complete,
                f"final_status={run.final_status}",
            )
        )

    if case.should_escalate is not None:
        escalated = run.final_status == "awaiting_approval" or any(
            event.operation == "escalation" for event in run.trajectory
        )
        checks.append(
            DeterministicCheck(
                "escalation_state",
                escalated == case.should_escalate,
                f"escalated={escalated}",
            )
        )

    if case.max_steps is not None:
        steps = sum(1 for event in run.trajectory if event.operation == "agent_step")
        checks.append(
            DeterministicCheck(
                "max_steps",
                steps <= case.max_steps,
                "ok" if steps <= case.max_steps else f"steps={steps}, max={case.max_steps}",
            )
        )

    return checks
