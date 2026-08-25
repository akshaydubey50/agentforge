"""OpenTelemetry export — the same run, in a tool built for reading runs.

TraceSpan rows power this repo's own trace explorer, and they are the durable
record. What they cannot do is open in Phoenix, Langfuse, Datadog or Jaeger,
which is where you go when the question is "why was this run slow" rather
than "what did it decide". This module answers that without a second
instrumentation pass: `graph.tracing.span()` already wraps every node and
every tool call, so one hook there emits a complete span tree.

OFF BY DEFAULT, AND OFF MEANS FREE

Nothing initializes until OTEL_EXPORTER_OTLP_ENDPOINT is set. Unset, `span()`
enters a null context manager and the SDK is never even imported, so a
deployment that doesn't want this pays nothing for it.

    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317 docker compose up

SEMANTIC CONVENTIONS

Spans carry `openinference.span.kind` (LLM / TOOL / CHAIN / AGENT), which is
what makes Phoenix render an agent run as an agent run rather than a flat
list of anonymous spans. The mapping from this codebase's span_type lives in
_KIND_FOR below and degrades to CHAIN for anything unrecognized -- a new
span_type shows up in the trace correctly shaped, just unclassified, rather
than not showing up.

FAIL-OPEN, same rule as events.py: an unreachable collector, a missing SDK,
a bad endpoint -- none of it is a reason for an agent run to die. The
TraceSpan row written alongside is unaffected either way.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from agentsys.config import settings

logger = logging.getLogger(__name__)

_tracer = None
_init_attempted = False

# span_type (see db/models.TraceSpan) -> OpenInference kind.
_KIND_FOR = {
    "sketch": "LLM",
    "agent_step": "LLM",
    "tool_selection": "LLM",
    "reasoning": "LLM",
    "review": "LLM",
    "synthesize": "LLM",
    "tool_call": "TOOL",
    "memory": "RETRIEVER",
    "escalation": "CHAIN",
}


def enabled() -> bool:
    return bool(settings.otel_exporter_otlp_endpoint)


def _get_tracer():
    """Build the tracer once, on first use. Returns None if OTel is off or
    unavailable, and remembers that so a broken setup is not retried on every
    single span."""
    global _tracer, _init_attempted
    if _init_attempted:
        return _tracer
    _init_attempted = True

    if not enabled():
        return None

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": settings.otel_service_name})
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint, insecure=True)
            )
        )
        # NOT trace.set_tracer_provider(): that installs a process-global and
        # would silently take over any provider the host application already
        # configured. A private provider keeps this additive.
        _tracer = provider.get_tracer("agentsys")
        logger.info("OTel export enabled -> %s", settings.otel_exporter_otlp_endpoint)
    except Exception as exc:  # noqa: BLE001 -- telemetry must not fail a run
        logger.warning("OTel export unavailable, continuing without it: %s", exc)
        _tracer = None
    return _tracer


@contextmanager
def span(name: str, *, span_type: str, task_id: str, subtask_id: str | None = None, **attributes):
    """Wrap a unit of work in an OTel span, or in nothing at all when export
    is off. Nesting is automatic: the SDK's context propagation makes a span
    opened inside another one its child, which is exactly the tree
    graph.tracing.span()'s call sites already form."""
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    try:
        with tracer.start_as_current_span(name) as otel_span:
            otel_span.set_attribute("openinference.span.kind", _KIND_FOR.get(span_type, "CHAIN"))
            otel_span.set_attribute("agentsys.span_type", span_type)
            otel_span.set_attribute("agentsys.task_id", task_id)
            if subtask_id:
                otel_span.set_attribute("agentsys.subtask_id", subtask_id)
            for key, value in attributes.items():
                if value is not None:
                    otel_span.set_attribute(f"agentsys.{key}", str(value)[:4096])
            yield otel_span
    except Exception as exc:  # noqa: BLE001
        # A failure INSIDE the wrapped body is re-raised by the with-block
        # above before reaching here; this only catches the SDK itself
        # misbehaving, which must not take the run down with it.
        logger.debug("OTel span failed: %s", exc)
        yield None


def set_error(otel_span, message: str) -> None:
    """Mark a span failed. No-op when export is off, so callers don't have to
    branch."""
    if otel_span is None:
        return
    try:
        from opentelemetry.trace import Status, StatusCode

        otel_span.set_status(Status(StatusCode.ERROR, message))
    except Exception:  # noqa: BLE001
        pass
