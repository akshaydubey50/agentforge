# Phase 7B Observability Note

Status: implemented in Phase 7B.

Phase 7B is production observability only. It does not start Phase 7C, add eval
frameworks, add guardrail frameworks, add Logfire, or make observability into
runtime state.

## Current TraceSpan Architecture

`agentsys.graph.tracing.span()` is the existing trace seam. Every graph node and
tool call that wants durable trace history enters this context manager. The seam
currently does three things:

1. Creates a `TraceSpan` row before the operation runs.
2. Publishes `span_start` and `span_end` live events through Redis/SSE.
3. Opens an optional OpenTelemetry span through `agentsys.otel.span()`.

`TraceSpan` rows remain the durable execution record. They store `task_id`,
`subtask_id`, `span_type`, `name`, `input`, `output`, `status`, `started_at`,
and `ended_at`.

## Current OpenTelemetry Implementation

`src/agentsys/otel.py` is the only active OpenTelemetry implementation.

- Direct dependencies already exist in `requirements.txt`:
  `opentelemetry-api`, `opentelemetry-sdk`, and
  `opentelemetry-exporter-otlp-proto-grpc`.
- Export is disabled unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- The SDK is imported lazily only when export is enabled.
- A private `TracerProvider` is used instead of installing a global provider.
- A `BatchSpanProcessor` sends spans to an OTLP/gRPC exporter.
- Export failure is fail-open and must not fail an AgentForge task.
- Spans already carry `openinference.span.kind`, `agentsys.span_type`,
  `agentsys.task_id`, and `agentsys.subtask_id`.

The pre-Phase-7B implementation was a useful skeleton but did not expose rich
LLM, tool, policy, verification, context, memory, or redaction metadata.

Phase 7B keeps that skeleton and enriches it. `graph.tracing.span()` still
creates the durable row, emits Redis/SSE events, and opens the optional OTel
span. Before the OTel span closes, it attaches safe attributes built by
`agentsys.telemetry.span_attributes()`.

## Current Live Event/SSE Architecture

`agentsys.events.publish()` writes one Redis pub/sub event per live update.
`agentsys.events_api.task_events()` exposes a task-scoped SSE stream.

Redis/SSE is product live-state transport. It is not replaced by OTel or
Langfuse. A client connecting mid-run gets a snapshot from Postgres and then
live events. Durable history still comes from `/v1/tasks/{id}/trace`.

## Current Token/Cost Recording

`agentsys.cost.record_llm_call()` writes one `LlmCall` row after each successful
model call. It records purpose, model, prompt tokens, completion tokens, cached
tokens, and a cached cost. Reads derive spend from stored token counts through
`pricing.py`.

`LlmCall` remains durable cost truth. OpenTelemetry receives safe copies of
token/cost metadata for observability only.

## Current Privacy Behavior

Existing privacy controls:

- Audit metadata is recursively redacted by key name.
- Gmail draft body is redacted from trace input.
- External text is wrapped as untrusted content at tool boundaries.
- NUL bytes are scrubbed before Postgres and event writes.
- Events truncate large payloads for browser/live use.

Remaining Phase 7B gap:

- There is no central redaction seam for external telemetry exports.

Phase 7B closes this gap for OTel export with `agentsys.telemetry`. The module
does not sanitize or mutate the runtime payload; it only builds external
telemetry attributes from allowlisted values.

## Telemetry Trust Boundary

AgentForge DB is internal runtime truth. External observability backends are
lower-trust telemetry sinks.

Rules:

- Do not alter runtime payloads just to sanitize telemetry.
- Prefer allowlisted scalar attributes over dumping arbitrary objects.
- Unknown or sensitive fields are omitted or replaced with a redaction marker.
- Full prompts, private documents, raw memory bodies, Gmail bodies, OAuth
  tokens, authorization headers, cookies, API keys, and credentials are not
  exported by default.

## Langfuse Decision

Current Langfuse guidance supports OpenTelemetry ingestion directly. Phase 7B
therefore uses the existing OTel boundary and does not add the Langfuse SDK.

Decision:

- Use OpenTelemetry-compatible attributes that Langfuse can ingest.
- Do not duplicate spans through manual Langfuse tracing.
- Reconsider the Langfuse SDK only if Phase 7B later needs SDK-only features
  such as prompt management or score APIs.
- LLM score/eval ingestion is out of scope for Phase 7B and belongs to a later
  evaluation phase, not runtime tracing.

## Redaction Strategy

Phase 7B adds one centralized telemetry sanitization seam.

Safe scalar metadata is exported:

- IDs needed for correlation.
- Span type/name/status.
- Model and token/cost counts.
- Tool name and safety classifications.
- Policy decision/risk/action type.
- Verification route/method flags.
- Context and memory counts/token metrics.
- Retry/dedupe/refusal booleans.

Sensitive or high-risk values are omitted/redacted:

- API keys, secrets, credentials.
- OAuth access/refresh tokens.
- Authorization headers, cookies, bearer values.
- Gmail bodies and draft bodies.
- Private document bodies.
- Raw memory bodies.
- Full prompts and responses by default.
- Raw DB rows and raw MCP/tool output bodies.
- Exception messages beyond bounded sanitized type/category.

Implementation:

- `agentsys.telemetry.span_attributes()` builds safe OTel span attributes from
  `TraceSpan` input/output.
- `agentsys.telemetry.llm_attributes()` builds safe LLM usage attributes from
  `LlmCall` metadata.
- `agentsys.telemetry.safe_error_attributes()` converts raw exception/provider
  messages into a bounded category such as `timeout`, `auth_error`,
  `rate_limit`, `validation_error`, `provider_error`, `tool_error`, or
  `policy_error`.
- Key summaries omit sensitive keys such as authorization, cookie, secret,
  token, refresh token, prompt, response, body, raw, and content.
- Explicitly allowlisted token-count metrics such as `selected_context_tokens`
  and `cached_tokens` remain exportable because they are counts, not secret
  token strings.

## Span Mapping

The implementation reuses current runtime boundaries rather than inventing a
second hierarchy.

Expected logical hierarchy:

```text
agent_task
  triage / quick_reply
  sketch
  memory retrieval / memory curation
  agent_step
    tool_selection
    tool_call
    policy decision metadata
    verification
  synthesize
```

Current AgentForge span types map to OTel/OpenInference kinds:

| AgentForge span type | OTel/OpenInference kind |
|---|---|
| `agent` | `AGENT` |
| `triage` | `LLM` |
| `quick_reply` | `LLM` |
| `sketch` | `LLM` |
| `agent_step` | `LLM` |
| `tool_selection` | `LLM` |
| `reasoning` | `LLM` |
| `review` | `LLM` |
| `synthesize` | `LLM` |
| `tool_call` | `TOOL` |
| `verification` | `CHAIN` |
| `memory` | `RETRIEVER` |
| `escalation` | `CHAIN` |
| `plan` | `CHAIN` |

Example safe trace:

```text
agent_task
  agent_step
    attrs:
      agentsys.task_id=task_123
      agentsys.span_id=span_abc
      agentsys.span_type=agent_step
      agentsys.context.usable_budget_tokens=22000
      agentsys.context.selected_tokens=18000
      agentsys.context.tokens_avoided=4000
      agentsys.memory.retrieved_count=7
      agentsys.memory.selected_count=3
  llm_usage.agent_step
    attrs:
      agentsys.trace_source=LlmCall
      agentsys.llm_call_id=llm_456
      agentsys.llm.model=openai/gpt-4o-mini
      agentsys.llm.input_tokens=1234
      agentsys.llm.output_tokens=221
      agentsys.llm.cached_tokens=512
      agentsys.llm.cost_usd=0.00123
  escalation_created
    attrs:
      agentsys.policy.decision=require_approval
      agentsys.policy.action_type=external_write
      agentsys.policy.risk=medium
  tool_call
    attrs:
      agentsys.tool.name=gmail_create_draft
      agentsys.tool.execution_safety=non_retryable_side_effect
      agentsys.tool.attempt_count=1
  verify_subtask
    attrs:
      agentsys.verification.route=pass
      agentsys.verification.method=gmail_draft
```

Intentionally not captured:

- Full prompt or response text.
- Gmail body or private email contents.
- OAuth access/refresh tokens or authorization headers.
- Cookie/session/credential values.
- Raw memory content.
- Private document bodies.
- Raw database rows.
- Raw MCP or tool output bodies.
- Raw exception/provider message text.

## Failure Semantics

Observability failures fail open:

- OTel disabled.
- OTel SDK unavailable.
- Collector unavailable.
- Exporter exception.
- Attribute serialization exception.

Product failures still propagate. Telemetry must not swallow the actual
exception from the graph/tool/LLM path.

`agentsys.cost.record_llm_call()` writes the durable `LlmCall` row first and
then attempts a fail-open `otel.record_llm_usage()` export. If that export
raises, the row remains committed and task execution continues.

## Sampling Strategy

The simple strategy is:

- Default behavior: rely on the configured OTel SDK/exporter.
- Development: 100% if `OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- Production: configure collector/SDK sampling outside AgentForge first.
- Always keep error, deny, approval, and verification-failure spans eligible
  for export by attaching safe attributes that make downstream sampling rules
  possible.

No sampling DSL is introduced in Phase 7B.

## Expected UI Support

The enriched telemetry should support future Execution Studio views:

- Run graph with duration/status.
- Node inspector with model/tokens/cost.
- Tool call metadata and retry/dedupe status.
- Policy and approval status.
- Verification route/result.
- Context budget, selected tokens, tokens avoided, and memory counts.
- Links from AgentForge task/span IDs to external trace UI.

Redis/SSE remains the live UI transport. OTel/Langfuse is observability and
analytics, not real-time product state.

## Analytics Boundary

`/v1/analytics` and `/v1/system/summary` remain computed from AgentForge's DB
tables (`Task`, `Subtask`, `TraceSpan`, `ToolCall`, `LlmCall`, `Escalation`).
OTel and Langfuse are not product/business-state stores. They may answer
operational questions such as latency, cost regression, and slow spans, but
they do not replace DB aggregation.

## Dependencies

No Phase 7B dependency was added. The existing OpenTelemetry SDK/exporter is
enough for the implemented export boundary. Langfuse remains an OTLP sink, not
a runtime SDK dependency.

## Explicitly Excluded Work

- No DeepEval.
- No NeMo Guardrails.
- No Guardrails AI.
- No RAGAS.
- No Logfire.
- No Langfuse SDK unless a concrete OTel gap is proven.
- No frontend UI.
- No prompt capture by default.
- No behavior change to planning, tools, policy, approval, execution,
  verification, memory, or synthesis.
