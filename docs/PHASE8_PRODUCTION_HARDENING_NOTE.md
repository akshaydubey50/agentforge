# Phase 8 Production Hardening Note

Phase 8 answers how AgentForge behaves under duplicate delivery, dependency
failure, crashes, overload, and restarts. It does not add AI features or change
the agent architecture.

## 1. Current Runtime Topology

AgentForge has a mixed API/worker topology:

- FastAPI (`agentsys.main`) accepts authenticated task requests.
- Celery (`agentsys.worker`) consumes `run_agent_task(task_id)` jobs from Redis.
- LangGraph (`agentsys.graph.build`) runs synchronously inside one Celery task.
- Postgres is the durable source of truth for users, sessions, tasks, subtasks,
  trace spans, LLM calls, escalations, tool calls, audit events, Google tokens,
  and memory metadata.
- Redis is the Celery broker/backend, SSE pub/sub transport, rate-limit store,
  task idempotency cache, cancellation flag store, and short-term scratchpad.
- Chroma stores vector indexes for long-term memory and RAG; Postgres metadata
  remains the durable memory truth.
- External dependencies include LLM providers through LiteLLM, Gmail/Drive/Photos
  through Google APIs, RAG over HTTP, MCP stdio servers, Docker for code
  execution, and optional OpenTelemetry export.

## 2. Task Lifecycle

1. `POST /v1/tasks` or `POST /v1/tasks/upload` creates a `Task` row in Postgres.
2. The API records a Redis idempotency mapping only after the task commit.
3. The API enqueues `run_agent_task.delay(task_id)` in Celery.
4. The worker initializes DB metadata, invokes `graph.runner.run_task(task_id)`,
   and LangGraph reads/writes state through Postgres on every node.
5. `route_entry` chooses triage, sketch, or resume based on existing subtasks and
   task status.
6. `agent_step_node` creates one subtask at a time, executes or gates a tool,
   verifies the result, reviews when needed, and loops until synthesize/escalate.
7. Terminal state is written to `Task.status`; SSE receives best-effort Redis
   notifications and durable trace state remains in Postgres.

## 3. Worker Lifecycle

Celery uses Redis as broker/backend with `task_acks_late=True`,
`worker_prefetch_multiplier=1`, soft and hard task time limits, and prefork in
Docker Compose. Worker boot runs `recover_stranded_tasks()` as a best-effort
sweep. A worker process runs one graph invocation synchronously until the task
completes, escalates, fails, cancels, or hits a Celery time limit.

## 4. Persistent vs Ephemeral State

Postgres-backed durable state:

- `Task`, `Subtask`, `TraceSpan`, `ToolCall`, `Escalation`, `Review`, `LlmCall`
- `User`, `UserSession`, `GoogleConnection`, `MemoryEntry`, `AuditEvent`
- Rolling conversation summaries on `Task`

Redis-only ephemeral state:

- Celery broker messages and result backend
- SSE pub/sub frames with no replay
- task idempotency keys
- per-user rate-limit counters
- cancellation flags
- unproductive-step streak and other short-term memory values
- OAuth CSRF state

Chroma-backed rebuildable state:

- Long-term memory embeddings/documents and RAG vector indexes.

## 5. Concurrency Model

Graph execution is sequential inside one worker process. Docker Compose defaults
to multiple Celery prefork workers, so separate processes can concurrently receive
duplicate Celery deliveries or duplicate resume jobs for the same `Task`.

Tool effect concurrency is already serialized by `execution.execute_tool()` for
non-read actions: it computes an `effect_key`, takes a transaction-scoped
Postgres advisory lock, checks prior `ToolCall` rows, inserts an in-flight
`ToolCall(success=NULL)`, commits, then performs the effect.

Task-level concurrency before Phase 8 had no explicit claim. Phase 8 adds a
Postgres advisory task claim around one graph invocation so duplicate workers
exit before `graph.invoke({"task_id": task_id})`.

## 6. Task Claiming Model

Before Phase 8, Celery queue delivery is the only work distribution mechanism.
There is no durable task lease table and no task advisory lock. Postgres status
transitions are not atomic claims; graph nodes read and write task state but do
not prevent two workers from progressing the same task.

Phase 8 uses a Postgres advisory lock scoped to `Task.id` for the lifetime of
one graph invocation. If the lock cannot be obtained, the second worker exits
without running the graph. This adds no schema and uses Postgres as the existing
durable coordination boundary.

## 7. Crash Model

Known crash boundaries:

- Task row committed but Celery message not delivered: the task remains
  `pending`; startup recovery now re-enqueues stale `pending` tasks after a
  grace window.
- Crash before first agent step: task may remain `pending` or `running` with no
  subtasks, depending on where it stopped.
- Crash during LLM call before persisting result: no model result exists; resume
  replans from durable state.
- Crash after subtask is marked `running`: `reconcile_orphaned_subtasks()` marks
  in-flight subtasks failed on resume.
- Crash after `ToolCall(success=NULL)` is committed and before/during execution:
  the row is ambiguous until recovery classifies it.
- Crash after an idempotent effect but before success persistence: recovery marks
  the ambiguous row failed so a repeat is allowed.
- Crash after a non-idempotent external effect before success persistence:
  recovery leaves the row ambiguous; future identical execution refuses.
- Crash during verification: subtask remains in-flight and is reconciled on
  resume; verification does not grant special privileges.
- Crash after approval accepted but before gated tool execution: escalation is
  approved and task is `running`; a resume can execute the stored snapshot once,
  still through policy/approval checks and the ToolCall ledger.
- Crash during memory curation: task is already completed; memory is best effort.

## 8. Startup Recovery

Startup recovery re-enqueues stale `Task(status=RUNNING)` rows whose
`updated_at` is older than `settings.stranded_task_grace_seconds` and stale
`Task(status=PENDING)` rows whose `updated_at` is older than
`settings.stale_pending_task_grace_seconds`.
`reconcile_orphaned_subtasks(task_id)` runs at the start of every invocation and
marks stranded subtask rows failed, then routes incomplete `ToolCall` rows by
tool execution safety.

Phase 8 makes the recovery sweep concurrency-safe with a recovery advisory claim
and relies on task advisory claims to prevent duplicate recovery resumes from
executing the same task concurrently.

## 9. Timeout Ownership

Primary timeout owners:

- LLM and embeddings: `agentsys.llm` passes `settings.llm_timeout_seconds`.
- Celery task wall clock: `task_soft_time_limit_seconds` and
  `task_time_limit_seconds`.
- Gmail/Drive/Photos HTTP: explicit `httpx` timeouts in each tool.
- RAG HTTP: `knowledge_search` uses a 60s timeout.
- Web search: Tavily 15s, DuckDuckGo 10s, hosted search through LLM timeout.
- MCP: connect/discovery/call timeouts in `mcp_tool.py`.
- DB read-only tool: Postgres `statement_timeout`.
- Code execution: tool-level container wait/kill timeout plus worker wall clock.
- SSE: bounded Redis pub/sub polling plus heartbeat.

Phase 8 should not add nested retry/timeout loops where one owner already exists.

## 10. Retry Ownership

Primary retry owners:

- LLM provider transport: `agentsys.llm` only.
- Tool execution: `agentsys.execution.run_with_retry()` only, and only for
  known-retryable failures of idempotent tools.
- Celery: infrastructure retry only for SQLAlchemy `OperationalError` and Redis
  connection errors in `run_agent_task`.
- Graph review/replan: semantic correction, not transport retry.
- Web search DuckDuckGo fallback has a local single retry for bot detection; it
  is an implementation-specific scrape fallback.

Risks to avoid: adding graph-level retries around LLM/tool calls would multiply
attempts.

## 11. Backoff Behavior

LLM and tool retries use bounded exponential equal jitter. Celery autoretry uses
Celery backoff for infrastructure errors. Tests should monkeypatch sleep/backoff
rather than waiting.

## 12. Backpressure Model

Current limits:

- Celery worker concurrency bounds simultaneous task processes.
- `worker_prefetch_multiplier=1` avoids reserving extra work per process.
- per-user API rate limiting bounds submitted task count per hour.
- task step/cost/time limits bound per-task spend.
- subagent depth/step limits bound delegation.

Remaining gaps:

- no LLM/tool semaphore inside a worker process.

Phase 8 keeps existing Celery concurrency as the worker execution bound, blocks
duplicate task execution by task advisory claim, and adds a small API-side
active-task overload guard.

## 13. Dependency Failure Behavior

- Postgres unavailable: effectful execution must fail closed because a durable
  ToolCall claim cannot be created or checked.
- Redis unavailable: Celery cannot enqueue/consume, SSE degrades, idempotency and
  rate-limit caches fail open, cancellation may be missed. Durable truth remains
  in Postgres.
- Chroma unavailable: memory retrieval/indexing fails open or records metadata
  failure; task correctness must not depend on Chroma.
- LLM transient failures retry in `llm.py`; permanent failures fail immediately.
- Gmail draft timeout is ambiguous and not blindly retried because the tool is
  `NON_RETRYABLE_SIDE_EFFECT`.
- MCP timeout is a failed tool result classified as timeout; automatic retry only
  occurs if the MCP server was declared idempotent.
- OpenTelemetry/export and Redis event publication failures must not corrupt
  durable execution state.

## 14. Graceful Shutdown Behavior

Current shutdown is mostly delegated to Celery/FastAPI defaults plus Celery soft
and hard time limits. There is no app-owned shutdown gate that stops new work
before SIGTERM, but recoverability comes from Postgres task/subtask state and
ToolCall ledger rows. Phase 8 should document this honestly and keep any added
shutdown logic minimal.

## 15. Health/Readiness Design

`GET /health` is liveness: process is alive, no business writes.

`GET /health/deep` is current readiness/diagnostics: read-only checks for
database, Chroma heartbeat, and Celery worker ping. It deliberately never creates
business records. It returns `status=degraded` instead of throwing for dependency
failure.

Phase 8 may add a clearer `/ready` alias or helper only if tests require a
readiness failure surface distinct from deep diagnostics.

## 16. Stuck-Task Model

A task is actually stuck when durable evidence says it is no longer progressing:

- `running` with `updated_at` older than the stranded-task grace window.
- in-flight subtask states surviving into a fresh invocation.
- `ToolCall(success=NULL)` tied to a stranded subtask.
- expired approval with no valid resume path.

A long task is not stuck while it is actively writing status/span/subtask
progress within the threshold.

## 17. Deployment Assumptions

Docker Compose starts Postgres, Redis, Chroma, runs migrations once, starts API
and worker, then the optional dashboards and RAG services. Railway-style single
service deploys rely on the Dockerfile command running `alembic upgrade head`
before Uvicorn. Workers need Redis, Postgres, and, for code execution, the Docker
socket.

## 18. Current Production Risks

1. Redis-backed idempotency is lost on Redis flush or outage, so duplicate API
   creates can happen without an idempotency key hit.
2. No full graceful worker drain exists beyond Celery behavior and recoverable
   durable state.
3. No in-process LLM/tool semaphore exists beyond Celery worker concurrency and
   per-task budgets.
4. API active-task fairness is a guardrail, not a scheduler; one user's accepted
   work can still consume global queue capacity until per-user limits bite.
5. Recovery postpones stale task re-enqueue until the next grace window if Redis
   fails after recovery updates `Task.updated_at`.

## 19. Minimal Implementation Plan

1. Postgres task advisory claim helpers.
2. Bounded startup recovery for stale `pending`/`running` tasks.
3. Small operational helpers for stuck-task detection, readiness, shutdown, and
   active-task overload.
4. Deterministic tests for task claim race, recovery idempotency, provider and
   dependency failures, readiness, and security recovery regression.
5. A synthetic local reliability benchmark using fake model/tools only.
6. A concise production runbook.

## 20. Explicitly Excluded Work

No Kubernetes, Kafka, Temporal, new queue, new model framework, new vector
database, new guardrail framework, new eval framework, billing, RBAC redesign,
enterprise tenancy, Gmail send, or Phase 9 UI work.
