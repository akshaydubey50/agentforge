"""Safe telemetry attributes for external observability sinks.

Postgres rows keep the runtime truth. This module only decides what may leave
the process as OpenTelemetry attributes, so callers use allowlisted metadata
instead of exporting arbitrary prompts, tool payloads, memory bodies, or
provider exception text.
"""

from __future__ import annotations

from typing import Any

from agentsys.guardrails import redact_secrets

REDACTED = "[REDACTED_FOR_TELEMETRY]"
SCHEMA_VERSION = "phase7b.v1"
MAX_STRING = 512

_SENSITIVE_KEY_FRAGMENTS = {
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "credential",
    "csrf",
    "jwt",
    "oauth",
    "passcode",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "session",
    "signature",
    "token",
}

_SENSITIVE_EXACT_KEYS = {
    "body",
    "code",
    "content",
    "email_body",
    "full_text",
    "message",
    "messages",
    "output",
    "pin",
    "prompt",
    "raw",
    "response",
    "state",
    "text",
}

_CONTEXT_METRICS = {
    "model_context_window_tokens": "agentsys.context.model_window_tokens",
    "configured_context_budget_tokens": "agentsys.context.configured_budget_tokens",
    "effective_context_window_tokens": "agentsys.context.effective_window_tokens",
    "reserved_output_tokens": "agentsys.context.reserved_output_tokens",
    "usable_input_budget_tokens": "agentsys.context.usable_budget_tokens",
    "raw_context_tokens": "agentsys.context.raw_tokens",
    "selected_context_tokens": "agentsys.context.selected_tokens",
    "selected_pre_compression_tokens": "agentsys.context.selected_pre_compression_tokens",
    "selected_post_compression_tokens": "agentsys.context.selected_post_compression_tokens",
    "protected_tokens": "agentsys.context.protected_tokens",
    "compression_ratio": "agentsys.context.compression_ratio",
    "tokens_avoided": "agentsys.context.tokens_avoided",
    "conversation_tokens": "agentsys.context.conversation_tokens",
    "summary_tokens": "agentsys.context.summary_tokens",
    "rolling_summary_tokens": "agentsys.context.rolling_summary_tokens",
    "recent_conversation_tokens": "agentsys.context.recent_conversation_tokens",
    "tool_rag_tokens": "agentsys.context.tool_rag_tokens",
    "memory_tokens": "agentsys.context.memory_tokens",
    "artifact_references": "agentsys.context.artifact_reference_count",
    "compressed_block_count": "agentsys.context.compressed_block_count",
    "compression_failure_count": "agentsys.context.compression_failure_count",
    "available_optional_tokens": "agentsys.context.available_optional_tokens",
    "memory_budget_tokens": "agentsys.context.memory_budget_tokens",
    "retrieved_memory_count": "agentsys.memory.retrieved_count",
    "selected_memory_count": "agentsys.memory.selected_count",
    "dropped_memory_count": "agentsys.memory.dropped_count",
    "duplicate_context_removed_count": "agentsys.memory.duplicate_context_removed_count",
    "selected_memory_tokens": "agentsys.memory.selected_tokens",
    "active_memory_count": "agentsys.memory.active_count",
    "archived_memory_count": "agentsys.memory.archived_count",
    "over_budget": "agentsys.context.over_budget",
}

_MEMORY_CURATION_METRICS = {
    "candidate_count": "agentsys.memory.candidate_count",
    "stored_count": "agentsys.memory.stored_count",
    "merged_count": "agentsys.memory.merged_count",
    "ignored_count": "agentsys.memory.ignored_count",
    "index_failed_count": "agentsys.memory.index_failed_count",
}

_TOOL_EXECUTION_METRICS = {
    "attempts": "agentsys.tool.attempt_count",
    "safety": "agentsys.tool.execution_safety",
    "failure_kind": "agentsys.tool.failure_kind",
    "retry_reason": "agentsys.tool.retry_reason",
    "deduped": "agentsys.tool.deduped",
    "refused_ambiguous": "agentsys.tool.refused_ambiguous",
    "effect": "agentsys.tool.effect",
}

_VERIFICATION_FIELDS = {
    "route": "agentsys.verification.route",
    "method": "agentsys.verification.method",
    "retryable": "agentsys.verification.retryable",
    "needs_replan": "agentsys.verification.needs_replan",
    "needs_human": "agentsys.verification.needs_human",
    "verified": "agentsys.verification.verified",
}

_GUARDRAIL_FIELDS = {
    "stage": "agentsys.guardrail.stage",
    "decision": "agentsys.guardrail.decision",
    "risk_type": "agentsys.guardrail.risk_type",
    "detector": "agentsys.guardrail.detector",
    "confidence": "agentsys.guardrail.confidence",
    "blocked": "agentsys.guardrail.blocked",
    "latency_ms": "agentsys.guardrail.latency_ms",
}


def is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_").replace(".", "_")
    if normalized in _SENSITIVE_EXACT_KEYS:
        return True
    return any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)


def sanitize_text(value: Any, *, max_length: int = MAX_STRING) -> str:
    text = redact_secrets(str(value), replacement=REDACTED)
    if len(text) > max_length:
        text = f"{text[:max_length]}...[truncated]"
    return text


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return sanitize_text(value)
    return None


def _set_safe(attrs: dict[str, str | int | float | bool], key: str, value: Any) -> None:
    safe = _safe_scalar(value)
    if safe is not None:
        attrs[key] = safe


def _keys_attr(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    keys = sorted(str(key) for key in value.keys() if not is_sensitive_key(str(key)))
    if not keys:
        return None
    return ",".join(keys)


def _safe_dict_values(
    source: dict[str, Any],
    mapping: dict[str, str],
    attrs: dict[str, str | int | float | bool],
) -> None:
    for source_key, attr_key in mapping.items():
        if source_key in source:
            _set_safe(attrs, attr_key, source[source_key])


def exception_category(message: Any) -> str:
    text = str(message or "").lower()
    if any(marker in text for marker in ("timeout", "timed out", "deadline")):
        return "timeout"
    if any(marker in text for marker in ("rate limit", "429", "too many requests")):
        return "rate_limit"
    if any(marker in text for marker in ("unauthorized", "forbidden", "401", "403", "auth")):
        return "auth_error"
    if any(marker in text for marker in ("connection", "dns", "network", "unreachable")):
        return "provider_error"
    if any(marker in text for marker in ("validation", "invalid arguments", "schema")):
        return "validation_error"
    if "tool" in text:
        return "tool_error"
    if any(marker in text for marker in ("policy", "denied", "refused")):
        return "policy_error"
    return "provider_error"


def safe_error_attributes(message: Any) -> dict[str, str | int | float | bool]:
    return {
        "agentsys.error.present": True,
        "agentsys.error.category": exception_category(message),
    }


def _output_summary(output: Any, attrs: dict[str, str | int | float | bool]) -> None:
    if not isinstance(output, dict):
        return
    keys = _keys_attr(output)
    if keys:
        attrs["agentsys.output.keys"] = keys

    for key in ("success", "rejected", "refused"):
        if key in output:
            if key == "success":
                _set_safe(attrs, "agentsys.tool.success", output[key])
            else:
                attrs[f"agentsys.{key}"] = True
                if key == "rejected" and "policy" in str(output[key]).lower():
                    attrs["agentsys.policy.decision"] = "deny"

    execution = output.get("execution")
    if isinstance(execution, dict):
        _safe_dict_values(execution, _TOOL_EXECUTION_METRICS, attrs)

    context = output.get("context")
    if isinstance(context, dict):
        _safe_dict_values(context, _CONTEXT_METRICS, attrs)

    _safe_dict_values(output, _MEMORY_CURATION_METRICS, attrs)
    _safe_dict_values(output, _VERIFICATION_FIELDS, attrs)

    guardrail = output.get("guardrail")
    if isinstance(guardrail, dict):
        _safe_dict_values(guardrail, _GUARDRAIL_FIELDS, attrs)
    guardrails = output.get("guardrails")
    if isinstance(guardrails, list) and guardrails:
        safe_items = [item for item in guardrails if isinstance(item, dict)]
        if safe_items:
            attrs["agentsys.guardrail.count"] = len(safe_items)
            if any(bool(item.get("blocked")) for item in safe_items):
                attrs["agentsys.guardrail.blocked"] = True

    if "error" in output:
        attrs.update(safe_error_attributes(output.get("error")))


def span_attributes(
    *,
    span_id: str | None,
    task_id: str,
    subtask_id: str | None,
    span_type: str,
    name: str,
    status: str,
    duration_ms: int,
    input: dict | None,
    output: dict | None,
) -> dict[str, str | int | float | bool]:
    attrs: dict[str, str | int | float | bool] = {
        "agentsys.telemetry.schema_version": SCHEMA_VERSION,
        "agentsys.trace_source": "TraceSpan",
        "agentsys.task_id": task_id,
        "agentsys.span_type": span_type,
        "agentsys.span_name": sanitize_text(name),
        "agentsys.status": sanitize_text(status),
        "agentsys.duration_ms": duration_ms,
        "langfuse.observation.type": "generation" if span_type in {"agent_step", "sketch", "tool_selection", "reasoning", "review", "synthesize", "triage", "quick_reply"} else "span",
    }
    if span_id:
        attrs["agentsys.span_id"] = span_id
    if subtask_id:
        attrs["agentsys.subtask_id"] = subtask_id
    if span_type == "tool_call":
        attrs["agentsys.tool.name"] = sanitize_text(name)
    if input:
        keys = _keys_attr(input)
        if keys:
            attrs["agentsys.input.keys"] = keys
        for key in (
            "steps_taken",
            "steps_taken_this_turn",
            "source",
            "tool_success",
            "kind",
            "tool_name",
            "policy_decision",
            "action_type",
            "risk",
        ):
            if key in input and not is_sensitive_key(key):
                _set_safe(attrs, f"agentsys.input.{key}", input[key])
        if "policy_decision" in input:
            _set_safe(attrs, "agentsys.policy.decision", input["policy_decision"])
        if "action_type" in input:
            _set_safe(attrs, "agentsys.policy.action_type", input["action_type"])
        if "risk" in input:
            _set_safe(attrs, "agentsys.policy.risk", input["risk"])

    _output_summary(output or {}, attrs)

    if status != "ok" and "agentsys.error.present" not in attrs:
        attrs.update(safe_error_attributes(status))
    return attrs


def llm_attributes(
    *,
    task_id: str,
    subtask_id: str | None,
    llm_call_id: str | None = None,
    purpose: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
    cost_usd: float,
    provider: str | None = None,
) -> dict[str, str | int | float | bool]:
    attrs: dict[str, str | int | float | bool] = {
        "agentsys.telemetry.schema_version": SCHEMA_VERSION,
        "agentsys.trace_source": "LlmCall",
        "agentsys.task_id": task_id,
        "agentsys.llm.purpose": sanitize_text(purpose),
        "agentsys.llm.model": sanitize_text(model),
        "agentsys.llm.input_tokens": prompt_tokens,
        "agentsys.llm.output_tokens": completion_tokens,
        "agentsys.llm.cached_tokens": cached_tokens,
        "agentsys.llm.cost_usd": float(round(cost_usd, 8)),
        "agentsys.llm.success": True,
        "gen_ai.request.model": sanitize_text(model),
        "gen_ai.usage.input_tokens": prompt_tokens,
        "gen_ai.usage.output_tokens": completion_tokens,
        "langfuse.observation.type": "generation",
        "langfuse.observation.input_tokens": prompt_tokens,
        "langfuse.observation.output_tokens": completion_tokens,
        "langfuse.observation.total_cost": float(round(cost_usd, 8)),
    }
    if llm_call_id:
        attrs["agentsys.llm_call_id"] = llm_call_id
    if subtask_id:
        attrs["agentsys.subtask_id"] = subtask_id
    if provider:
        attrs["agentsys.llm.provider"] = sanitize_text(provider)
        attrs["gen_ai.system"] = sanitize_text(provider)
    return attrs
