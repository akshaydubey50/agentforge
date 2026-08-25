# AgentForge — architecture audit and migration plan

Scope: the whole repository as of `28ef3bd` (branch `feature/agent-memory-retrieval`).
Nothing in the code was changed to produce this. Companion to
[ROADMAP.md](ROADMAP.md), which tracks a *legibility* theme; this document
tracks a different one — **can this system safely take real actions on a
user's behalf, and prove it did.**

---

## 1. Current architecture

### 1.1 Shape

Two independent Python packages in one repository, joined by exactly one HTTP
call:

```
src/agentsys   the agent       FastAPI + Celery worker + LangGraph
src/rag        retrieval       FastAPI (separate container)
                               ^ agentsys calls POST /v1/ask
                                 via tools/knowledge_search.py
```

They share `requirements.txt`, the Dockerfile image and `.env`, and nothing
else — separate `config.py`, separate `llm.py`, separate `auth.py`, separate
`security_headers.py`, separate `eval/`, and two different Chroma modes
(agentsys uses the Chroma *server* for agent memory; rag uses Chroma
*embedded* against `./data/chroma`).

### 1.2 agentsys runtime

| Concern | Where |
|---|---|
| HTTP API | `main.py` + 5 routers (`auth_router`, `system_api`, `events_api`, `artifacts_api`, `integrations/router`) |
| Async execution | Celery on Redis, prefork pool, `acks_late`, soft/hard wall-clock limits (`worker.py`) |
| Orchestration | LangGraph, 6 nodes, compiled once per process (`graph/build.py`) |
| State of record | Postgres via SQLModel (`db/models.py`) — 14 tables |
| Ephemeral state | Redis — session cache, cancel flags, idempotency keys, dead-call ledger, unproductive streak |
| Semantic memory | Chroma (vectors) + `MemoryEntry` (metadata) |
| Tools | ~14 in a process-global registry, including MCP-discovered ones |
| Observability | `TraceSpan` rows + Redis pub/sub events + optional OTel export — all from one seam (`graph/tracing.py`) |
| Audit | `AuditEvent` hash chain (`audit.py`) |
| Eval | 3-tier gate: unit → deterministic battery → LLM judge (`eval/gate.py`) |
| UI | Next.js dashboard (`web/`) **plus** three Streamlit apps |

### 1.3 The graph

```
                     route_entry
                          |
              resume?  ---+---  new turn?
                 |                  |
                 |               triage --quick--> quick_reply --> END
                 |                  |                   |
                 |                full             (on error)
                 |                  |                   |
                 |                  v                   v
                 |              sketch --low conf--> escalate --> END
                 |                  |                   ^
                 |                  v                   |
                 +---------->  agent_step  -------------+
                              (self-loop)   budget / cost /
                                   |        unproductive /
                                finish      review-exhausted /
                                   |        approval-gate
                                   v
                              synthesize --> END
```

`AgentState` is **`{task_id, route}` and nothing else.** Every other fact is
re-read from Postgres on each node entry. That is a deliberate, correct and
unusually disciplined choice — it is what lets a human approval resume the
task in a *different worker process* with no checkpoint restore.

### 1.4 What "multi-agent" means here

Nothing structural. "Supervisor", "Specialist" and "Reviewer" are three
prompts invoked inside node functions — not separate agents, queues or
policies. The only genuinely second actor is `delegate_subagent`, a *tool*
that runs its own bounded tool-use loop (depth capped at 1).

Collapsing the old multi-agent design into one loop was the right call and
should not be undone. It is worth renaming in the docs, though: the current
naming implies an agent topology that no longer exists.

---

## 2. Current request lifecycle

**Submit.** `POST /v1/tasks` → session cookie resolved to a `User` → per-user
hourly rate limit → `Idempotency-Key` looked up in Redis → `Task` row
committed → key recorded → `run_agent_task.delay(task_id)`.

**Pick up.** Celery prefork child runs `run_task(task_id)` →
`reconcile_orphaned_subtasks` marks any `RUNNING`/`NEEDS_REVISION` subtask
left by a dead run as `FAILED` → one root OTel span → `graph.invoke`.

**Route in.** `route_entry` asks *resume or new turn?* — measured as "does any
subtask exist since `_turn_start` (last `TaskMessage`, else task creation)".
Resume → straight to `agent_step`. New turn → `triage`.

**Triage.** One cheap classifier call. `quick` → single-call reply, task
`COMPLETED`, done. `full` → `sketch` (or `agent_step` if subtasks already
exist). Any error fails open to `full`.

**Sketch.** Retrieves `k=3` memories, produces a non-binding `outline` +
`confidence`. Persisted **only as a `TraceSpan` row**. Confidence < 3 →
`plan` escalation.

**The loop** (`agent_step_node`, one invocation per step):

1. Four stop conditions, checked *before* spending anything — per-turn step
   count ≥ 12, task spend ≥ $1.00, unproductive streak ≥ 3, cancel flag.
2. Rebuild context: current plan (latest `plan` TraceSpan, else the sketch),
   prior context (**only `DONE` subtasks**; last 3 verbatim, older truncated
   to 300 chars), conversation history, dead-call list.
3. **One** structured call →
   `NextStepDecision{next_action, subtask_description, tool_name,
   tool_input_json, updated_plan, rationale}`.
4. `finish` → `synthesize`. `act` → create a `Subtask` row (`RUNNING`).
5. `_execute_subtask`: inject plumbing kwargs (`task_id`, `user_id`, …) →
   `needs_approval(kwargs)`? → gate: subtask `ESCALATED`, `tool_approval`
   escalation, **return sentinel** → dead-call check → run the tool → record
   `ToolCall` + `tool_call` span → spill if > 12k chars → detect
   `_awaiting_human`.
6. `_review_subtask`: LLM reviewer (deliberately a stronger model) → `pass`
   (subtask `DONE`, loop on) / `reject` (retry, ≤ 2 attempts, with the
   reviewer's feedback) / `escalate`.
7. Self-loop back to 1.

**Escalate.** Task → `AWAITING_APPROVAL`, graph ends. A human decides via
`POST /v1/escalations/{id}/decide` → `apply_escalation_decision` → audit row
(and a second audit row if a gated tool actually ran) → re-enqueue →
`route_entry` sends it back into the loop.

**Synthesize.** Combine subtask outputs (spill plumbing stripped by
`artifacts.for_synthesis`) → final answer → task `COMPLETED` → memory
reflection (saved only if generalizable) → prune → clear Redis working memory.

**Throughout.** Every node and tool call writes a `TraceSpan`, publishes
`span_start`/`span_end` to Redis pub/sub (SSE to the browser), and optionally
emits an OTel span. Every LLM call writes an `LlmCall` row.

---

## 3. What is good and should remain

These are not table stakes; several are things most agent codebases get wrong.

1. **Postgres is the source of truth; graph state is nearly empty.** The
   single best decision in the codebase. Keep it absolutely.
2. **One instrumentation seam, three sinks.** `graph/tracing.span()` writes
   the durable row, publishes the live event and opens the OTel span. A fourth
   subscriber touches one file. Telemetry is fail-open everywhere.
3. **The audit hash chain**, including the honest docstring about what it does
   *not* prove without an external anchor.
4. **The eval gate's tier ordering** — free/deterministic first, model-judged
   last and threshold-only, with append-only run history. This is the correct
   shape and the thing that makes every later change measurable.
5. **Measured context engineering.** `artifacts.spill`, `deadcalls` and the
   `_gather_prior_context` truncation all cite the real runs that motivated
   them. The `_is_artifact_read` exemption (the escape hatch was sitting
   behind the door it exists to open) is a genuinely subtle bug, correctly
   fixed.
6. **Cost derived from tokens at read time**, with `cached_tokens` nullable so
   "unknown" and "none" stay distinguishable.
7. **Failure-mode discipline in the worker**: `acks_late` with
   `reject_on_worker_lost=False`, and the written reasoning for why a hung
   task must *not* be redelivered.
8. **Crash recovery in two tiers** (per-invocation reconcile + boot-time
   stranded sweep), with the residual hole documented rather than hidden.
9. **Idempotency keys at the API boundary**, keyed on `(user_id, key)`.
10. **Graceful tool degradation** — a tool that can't import, or an MCP server
    that's down, removes itself from the advertised set rather than appearing
    and failing on every call.
11. **`wrap_untrusted` being idempotent**, with the measured 28KB → 2.6MB
    nesting blowup explained.
12. **The comment culture.** Nearly every non-obvious decision carries its
    evidence. Preserve this — it is why this audit was possible at all.

---

## 4. What is unnecessarily complex

### 4.1 The repository is two products

`src/rag` duplicates config, LLM access, auth, security headers, eval and
dashboards. It runs its own FastAPI, its own Chroma mode, and has its own
tenancy model (none). The agent uses roughly 1% of its surface: one
`POST /v1/ask`.

The cost is not aesthetic — it is a real security divergence (§7.1, §7.2) and
two of everything to keep in sync.

### 4.2 Observability is being used as the state store

`sketch` and, more importantly, **the live plan** are persisted as `TraceSpan`
rows and re-read with "latest span where `span_type = 'plan'`". A trace
retention policy, a span-table cleanup or a partitioning change silently
destroys agent state. The plan is *state*; it belongs in a table.

### 4.3 Dead schema from the previous architecture

- `Subtask.depends_on` — written as `[]` at the only creation site
  (`nodes.py:814`), never read for anything but API output.
- `SubtaskStatus.PENDING / READY / SKIPPED` — never assigned anywhere.
- `settings.review_escalation_threshold` — never referenced.
- `short_term.get_all()` — no callers.

These are fossils of the old plan-then-schedule design and imply a dependency
graph that no longer exists.

### 4.4 One decision, two prompt paths

`agent_step_node` already picks the tool and its arguments. `_execute_subtask`
then re-wraps that choice as a `tool_selection` span; on a **retry** it
discards the choice and re-asks a completely different prompt
(`TOOL_SELECTION_PROMPT`). So one decision has two prompt implementations that
can drift — and the retry path is the one that never sees the plan.

### 4.5 `delegate_subagent` is a second agent loop

Its own prompt, own schema (`SubAgentStep`), own table, own step cap, own
depth cap — parallel to and duplicating the main loop, capped at exactly one
level of nesting. It also bypasses the parent's budgets: its steps don't count
toward `max_task_steps` and its failures never touch the unproductive streak.

### 4.6 Four UIs

Next.js (`web/`) plus `agentsys/dashboard.py`, `rag/dashboard.py`,
`rag/simple_upload.py` and `rag/upload_dashboard.py` — three Streamlit apps,
two of them wired into docker-compose.

### 4.7 `tweet_workshop`

A 257-line demo tool with its own internal generate/evaluate/optimize loop and
its own iteration cap, advertised to the model on every single request.

---

## 5. What is missing for a real action-taking agent

This is the core finding. **The system has never executed an irreversible
external side effect.** Every tool is read-only except `file_io` write (to a
per-task sandbox) and `code_execution` (network-disabled, ephemeral
container). There is no send, create, post, transfer or delete anywhere.

That matters because the safety machinery has therefore never been exercised
against the thing it exists for, and several load-bearing pieces turn out to
be absent when you look for them.

### 5.1 No typed tool contracts — *closed in Phase 1*

Tool arguments are described in **prose**, inside the `description` string the
LLM reads. The model emits `tool_input_json` as free text. `_execute_subtask`
does `json.loads` (falling back to `{}` on a parse error — silently, and it
then *calls the tool with no arguments*), then `**kwargs` straight into
`run()`. The only guard is a `TypeError` catch.

There is no JSON Schema, no Pydantic args model, no required-field check, no
type coercion, no enum validation, and no way to render a proposed call to a
human approver as anything but a raw dict.

This is the single largest gap against "deterministic tool execution".

### 5.2 No policy or risk layer

Approval is a **boolean on a Python class**. Two of fourteen tools set it.
The system cannot express any of:

- risk tiers (read / write / irreversible / financial)
- argument-dependent risk ("email to an external domain", "amount > X",
  "recipient not in the user's contacts")
- per-user, per-org or per-environment policy
- allow/deny lists, time-of-day windows, spend caps *per action*
- dry-run or preview-before-commit
- who is allowed to approve what (any signed-in owner can approve anything)
- approval expiry (a `tool_approval` escalation approved three days later
  re-executes with arguments chosen against a three-day-old world)
- step-up authentication — `UserSession.mfa_satisfied_at` exists in the schema
  and is **never read**

Policy is currently a property of code, not of configuration, and it is
per-tool rather than per-call.

### 5.3 No tool-call idempotency

`recovery.py` states the hole plainly: *"a re-run can re-execute a
side-effecting tool call that already happened before the crash."*

There is no per-call idempotency key, no effect ledger, and no
"was this already executed?" check *before* execution on resume. The dead-call
ledger records failures and repeat successes for *loop control*, in Redis,
cleared at the end of the task — it is not a durable execution record and was
never meant to be one.

The moment a real `send_email` tool exists, a worker crash between "email
sent" and "ToolCall committed" sends the email twice.

### 5.4 No verification stage

The reviewer judges **one step's output text** against **that step's own
description**. Nothing verifies:

- that the *goal* is met, end to end, before `synthesize` runs
- that a side effect actually landed (read-after-write)
- deterministic postconditions ("the file exists", "the row count changed",
  "the API returned 201 and an id")

The requested lifecycle has Observation and Verification as separate stages
from step review. Today there is only step review, and it is a single LLM
opinion with no evidence beyond the text it is handed.

### 5.5 No structured plan

The plan is `list[str]` inside a trace span. There are no step ids, no
dependencies, no per-step success criteria, no per-step status, and no way to
revise one step — `updated_plan` replaces the entire list wholesale. Replanning
is therefore text substitution, not a diff, and nothing can be verified
against the plan because the plan has no checkable structure.

### 5.6 No compensation or rollback

No saga pattern, no undo records, no way to reverse a completed step when a
later step fails. For a read-only system that is fine; for an action-taking
one it is the difference between a failed task and a half-applied change.

### 5.7 No resilience around the model call

`llm.py` calls `litellm.completion` bare: **no timeout, no retry, no backoff,
no fallback model, no rate-limit handling.** A provider holding a socket open
is caught only by Celery's 20-minute wall clock. With `concurrency=4`, four
hung calls stall the entire worker.

### 5.8 No resilience around the tool call

No per-tool timeout, no retry-with-backoff for transient failures, no circuit
breaker, no bulkhead. `web_search`, `gmail_*`, `google_drive_*` and
`knowledge_search` all make network calls; only `knowledge_search` sets a
timeout at all (60s).

### 5.9 Other gaps

- **No parallelism.** Documented, deliberate, and a real ceiling on latency.
- **No goal analysis stage.** The requested lifecycle starts with it; today
  `triage` (conversational vs. not) and `sketch` (rough outline) are the only
  pre-planning steps. Nothing extracts constraints, success criteria or
  ambiguities from the goal.
- **No per-user tool sets.** `get_registry()` is a process-global singleton
  built once at import; an MCP config change needs a restart, and a
  per-tenant capability set is impossible.
- **No structured logging or correlation ids** in application logs (spans are
  well covered; `logging` output is not).

---

## 6. Architectural risks

**R1 — Approving a budget escalation loops forever.** *(confirmed defect)*
`apply_escalation_decision` on a plan-level `budget` escalation sets the task
`RUNNING` and re-enqueues. `route_entry` sees work this turn → `agent_step` →
which recomputes `steps_taken_this_turn` from the same unchanged subtask rows
→ still ≥ `max_task_steps` → creates **another** budget escalation →
`AWAITING_APPROVAL`. The same applies to the unproductive-streak escalation
(the streak is never reset on resume). Approving is a no-op that manufactures
a new escalation each time.

**R2 — Agent state lives in the trace table.** See §4.2. Any trace retention
or cleanup policy is a data-loss event for the plan.

**R3 — Failed steps are invisible to the next decision.**
`_gather_prior_context` filters on `status == DONE`. `escalations.py` comments
that after a rejected/failed gated tool "the next agent_step decision sees the
failure in prior_context and can adapt" — it does not. The only carrier of
failure across steps is the dead-call ledger (a bare signature list) and the
subtask output the model never sees.

**R4 — Prompt injection has a short path to action.** `wrap_untrusted` is one
mitigation layer and honestly described as such. The second layer — the
approval gate — covers 2 of 14 tools. The agent reads attacker-influenceable
content (Gmail bodies, Drive files, web pages, uploaded documents) and, today,
can only act on it by writing a sandboxed file or running sandboxed code. Add
one outbound tool without a policy layer first and that changes completely.

**R5 — `delegate_subagent` escapes the parent's governance.** Its steps don't
count toward the step budget; its failures don't affect the unproductive
streak; its tool calls are gated by the same `needs_approval` check but its
loop has no cost ceiling of its own (only a 4-step cap).

**R6 — The reviewer reviews prose, not effects.** A tool can succeed at
exactly the wrong thing and pass review, because the reviewer is handed the
step description and the output text and nothing else.

**R7 — One task is one long-lived process.** A whole task runs inside a single
Celery task and a single `graph.invoke`. A resume is a fresh trace on
(possibly) a different worker — honest, but it means a long task holds a
prefork child for its entire duration and the OTel trace fragments at every
human decision.

**R8 — `json.loads` failure degrades to `{}`.** *(closed in Phase 1)* In `_execute_subtask`, a
malformed `tool_input_json` results in the tool being called with **no
arguments** rather than the call being rejected. For a read tool that is a
confusing error; for a write tool it is an unpredictable one.

---

## 7. Production risks

Ordered by severity.

### 7.1 `db_query` can read the entire application database — CRITICAL

`DbQueryTool` runs against `get_engine()` — the app's own Postgres, with the
app's own credentials. The guard is three regexes: starts with `SELECT`/`WITH`,
no DML keywords, single statement. **Nothing restricts which table is read.**

The description says `sample_metric`, but the following are all valid and
would succeed:

```sql
SELECT email, google_sub FROM app_user;
SELECT token_hash, user_id FROM user_session;
SELECT google_email, access_token, refresh_token FROM googleconnection;
```

`GoogleConnection.access_token` and `refresh_token` are **plaintext columns**.
So a single successful prompt injection in an email body or an uploaded
document can exfiltrate every connected user's Google OAuth refresh tokens
through a tool that is not approval-gated, via an agent whose output is then
shown to the requesting user.

This needs a dedicated read-only database role restricted to an explicit table
allowlist, not a regex.

### 7.2 The RAG corpus is a shared global tenancy — HIGH

agentsys enforces per-user isolation everywhere (`Task.owner_id`,
`MemoryEntry.owner_id`, `owner_id` in Chroma metadata). `src/rag` has no user
model at all: documents are a single shared corpus. Any user's
`knowledge_search` call retrieves any other user's uploaded documents. This is
documented in `rag/auth.py` as a deliberate scope cut, but it is a
cross-tenant data leak the moment there is a second user.

### 7.3 `POST /v1/ask` is unauthenticated and published on a host port — HIGH

`rag/main.py`'s `ask` has no `require_session` dependency (deliberately, so
the worker can call it server-to-server), and docker-compose publishes
`rag-api` on `8000:8000`. Anyone who can reach that port can query the entire
knowledge base and spend LLM budget, with no rate limit and no audit trail.
The fix is a service-to-service credential, not an open endpoint.

### 7.4 The worker mounts the host Docker socket — HIGH

`/var/run/docker.sock:/var/run/docker.sock` gives the worker container
capabilities equivalent to host root. It is needed for `code_execution`'s
sibling-container model. Combined with §7.1 and the injection surface, the
blast radius of one compromised agent run is the host.

### 7.5 No LLM timeout — MEDIUM/HIGH

§5.7. A hung provider call holds a prefork child for up to 20 minutes; four of
them stall the worker completely. This is the most likely *availability*
incident in the current design.

### 7.6 `/health/deep` creates a database row per call — MEDIUM

`ping_task` inserts a real `Task` row on every deep health check and the
endpoint blocks for up to 15 seconds waiting on Celery. Pointed at a load
balancer's health check, this is unbounded table growth plus a request that
can hang.

### 7.7 Deployment defaults — MEDIUM

- Hardcoded `agent:agent` Postgres credentials in docker-compose.
- Every service publishes to a host port, including Postgres and Redis.
- OAuth tokens stored plaintext (no column encryption, no KMS).
- No secret manager; `.env` is the mechanism everywhere.
- `init_db()`'s `create_all()` runs in the API lifespan *alongside* Alembic —
  correctly documented as additive-only, but it means a missed migration
  produces a partially-correct schema at runtime instead of a hard failure.

### 7.8 Untracked junk

`bash.exe.stackdump` in the repo root (already noted in ROADMAP's loose ends).

---

## 8. Suggested target architecture

The organising principle:

> **The LLM proposes. Deterministic code disposes.**
> A model may decide *what to attempt*. It may never decide whether an action
> is allowed, whether it already ran, how many times to retry it, or whether
> it succeeded.

The requested lifecycle needs four things this codebase doesn't have: validated
tool arguments, a policy check, an execution record that survives a crash, and
verification. It does **not** need new packages to get them — most of the
machinery already exists under a different name.

### 8.1 Reuse map — what already does the job

| Need | Already in the repo | Gap to close |
|---|---|---|
| Canonical `(tool, args)` identity | `deadcalls._signature()` — sorted keys, strips injected kwargs | none; hash it |
| Execution record | `ToolCall` — subtask, tool, input, output, success, latency | add `args_hash` + a lookup before executing |
| Human approval | `Escalation` + `apply_escalation_decision` | policy decides *when*, instead of a bool on a class |
| Step review | `_review_subtask` | becomes the fallback when no deterministic check exists |
| Retry / timeout / fallback model | **litellm supports `timeout=`, `num_retries=`, `fallbacks=`** | pass them |
| Per-task budgets | four stop conditions in `agent_step_node` | none |
| Audit | `audit.record` + hash chain | record the policy decision |

### 8.2 The whole design, as a diff

**Typed tool args** — `tools/base.py` gains one field, `_execute_subtask` gains
one validation step:

```python
class Tool(ABC):
    args_model: type[BaseModel] | None = None
    risk: Risk = Risk.READ
```

Validate before calling; a validation error becomes a failed `ToolResult` with
the pydantic message, which the existing reject-and-retry loop already handles.
The prompt gets `args_model.model_json_schema()` alongside today's prose
description. This deletes the `TypeError` catch and the `json.loads` → `{}`
degradation (R8).

**Policy** — one module, `policy.py`, one function:

```python
def decide(tool: Tool, args: BaseModel, user: User) -> Decision  # allow | approve | deny + reason
```

Rules live as a list in that file. Replaces `requires_approval`/
`needs_approval`. Move to YAML the day a non-developer needs to edit rules —
not before.

**Effect ledger** — no new table. `ToolCall` gains `args_hash`, plus a lookup
before execution:

```python
prior = session.exec(select(ToolCall).where(
    ToolCall.task_id == task_id, ToolCall.tool_name == name,
    ToolCall.args_hash == h, ToolCall.success == True)).first()
if prior and tool.risk >= Risk.WRITE:
    return True, json.dumps(prior.output)   # already happened; don't repeat it
```

That is the whole of §5.3. `ToolCall` needs `task_id` denormalised onto it (it
only has `subtask_id` today) so the lookup is one query.

**Plan as state** — `Task.plan` JSONB column holding
`[{step, done, criteria}]`. Not `Plan` + `PlanStep` tables: execution is
strictly sequential, so per-step dependency rows would model a graph that
cannot exist yet. Add the tables the day parallel steps land (ROADMAP G12).

**Verification** — `verify.py`. A step's `criteria` string is matched against a
small checker table (`file_exists`, `nonempty`, `rows > n`, `status == n`); an
unmatched criterion falls through to today's LLM reviewer. Goal-level check
before `synthesize` is the same function over the whole plan.

**LLM resilience** — `llm.py`, two lines:

```python
litellm.completion(..., timeout=60, num_retries=2)
```

### 8.3 Deliberately not building

| Skipped | Add when |
|---|---|
| `execution/breaker.py` circuit breaker | a provider outage actually takes the worker down |
| `planning/`, `policy/`, `execution/`, `verify/` as packages | any one of them exceeds ~300 lines |
| `Plan` / `PlanStep` / `Action` / `ActionAttempt` tables | parallel steps land and steps need real dependencies |
| Saga / compensation / rollback | a task ships more than one irreversible action per run |
| `GoalSpec` extraction stage | plan-level `criteria` prove insufficient in eval |
| Approval expiry, step-up auth | the first genuinely irreversible tool exists (Phase 5) |
| Per-user tool registries | a second tenant needs a different tool set |

### 8.4 Unchanged

Graph shape, Postgres-as-truth, the tracing seam, the audit chain, the eval
gate, spill/artifacts, dead-call ledgering, cancellation, two-tier crash
recovery. This is a layering change, not a rewrite.

---

## 9. Exact modules and files

### 9.1 Retain unchanged

```
graph/build.py, graph/state.py, graph/tracing.py
events.py, events_api.py, otel.py, audit.py, sanitize.py
artifacts.py, artifacts_api.py, deadcalls.py, cancellation.py
idempotency.py, ratelimit.py, security_headers.py
auth.py, auth_router.py, integrations/*
cost.py, pricing.py, memory/*, db/session.py
eval/gate.py, eval/battery.py, eval/grounding.py
web/   (except the stale security copy in settings/page.tsx)
```

### 9.2 Refactor

| File | Change | Size |
|---|---|---|
| `tools/db_query.py` | **Security.** Read-only role + table allowlist parsed from the query | ~30 lines |
| `llm.py` | `timeout=`, `num_retries=` on both call sites | 2 lines |
| `escalations.py` | Fix R1: reset per-turn budget + streak on resume | ~10 lines |
| `tools/base.py` | `args_model`, `risk`; delete `requires_approval`/`needs_approval` | ~15 lines |
| every `tools/*.py` | one pydantic args model each | ~10 lines each |
| `db/models.py` | `ToolCall.args_hash` + `ToolCall.task_id`; `Task.plan`; drop `Subtask.depends_on` | 1 migration |
| `graph/nodes.py` | Plan reads/writes `Task.plan` not TraceSpan; validate args; call `policy.decide`; ledger lookup; collapse §4.4's duplicate prompt path | net **shrinks** |
| `rag/main.py` | Service credential on `/v1/ask`; per-user document scoping | ~20 lines |
| `main.py` | `/health/deep` stops creating `Task` rows | ~5 lines |
| `docker-compose.yml` | Unpublish Postgres/Redis/rag-api host ports | config |

`nodes.py` at 1,076 lines is long, but splitting it is cosmetic — do it only if
a real change becomes hard to make there. The plan/policy/ledger edits above
remove more from it than they add.

### 9.3 Delete

```
tools/tweet_workshop.py                  demo, not a capability
rag/dashboard.py, rag/upload_dashboard.py, rag/simple_upload.py
agentsys/dashboard.py                    all four superseded by web/
memory/short_term.get_all()              no callers
bash.exe.stackdump                       untracked junk
.claude/worktrees/                       stale duplicate checkout
SubtaskStatus.PENDING/READY/SKIPPED      never assigned
settings.review_escalation_threshold     never referenced
graph/schemas.ToolChoice                 once §4.4 collapses
graph/prompts.TOOL_SELECTION_PROMPT      same
```

Reconsider `delegate_subagent` once `Task.plan` can grow mid-run — that covers
most of what it exists for.

### 9.4 Create

```
src/agentsys/policy.py              decide() + the rule list          ~120 lines
src/agentsys/verify.py              checker table + LLM fallback      ~100 lines

tests/test_policy.py                pure  -> gate tier 1
tests/test_tool_args.py             pure  -> gate tier 1
tests/test_tool_ledger.py           pure  -> gate tier 1
tests/test_escalation_resume.py     regression for R1
docs/SECURITY.md                    threat model, incl. the injection path
```

Two new modules, five new test files. No new packages, no new tables.

---

## 10. Migration plan, in implementation order

Every phase ends with `make gate` green.

### Phase 0 — stop the bleeding — **DONE**

1. ~~`db_query` table allowlist + dedicated read-only Postgres role (§7.1)~~
2. ~~Service credential on `rag /v1/ask` (§7.3)~~ — host port NOT unpublished,
   see remaining risks below
3. ~~Timeout, bounded retries, jittered backoff in `llm.py` (§7.5)~~
4. ~~Fix R1, with `tests/test_escalation_resume.py`~~
5. ~~`/health/deep` stops creating `Task` rows (§7.6)~~
6. ~~`rm bash.exe.stackdump`, remove the stale worktree~~

*Exit criteria met: an injected `SELECT` cannot reach `app_user`,
`user_session`, `googleconnection` or `audit_event`; approving a budget
escalation resumes work instead of manufacturing another escalation.*

**Corrections found while implementing** (the audit was wrong or incomplete
on these):

- **§8.2 was wrong about the LLM fix being two lines.** It proposed
  `litellm.completion(..., num_retries=2)`. litellm's built-in retry does not
  distinguish permanent from transient failures, so a bad API key would have
  been retried three times — the exact waste the requirement forbids. The
  classification has to be ours. ~90 lines in `llm.py`, still at the one seam.
- **§8.2's `num_retries` would also have broken embeddings.** A single
  `llm_fallback_model` naming a chat model must never be applied to
  `embed_texts`; the fallback is now opt-out and off for embeddings.
- **R1 was worse than described.** The audit named the step budget and the
  unproductive streak. The **cost ceiling** has the same defect and is the
  subtlest instance: lifetime spend can never decrease, so approving a cost
  escalation could never clear the guard. Also the zero-steps finish guard,
  which keys on lifetime `steps_taken`. Four guards, not two.
- **R1's fix needed no new state.** `Escalation.decided_at` on the most recent
  approved budget escalation already is the "continue from here" marker, so
  the window moved with zero schema change — but it must NOT move
  `_turn_start` itself, or `route_entry` sends a resumed task back through
  triage, which it documents must never happen. Two separate functions.
- **§7.6 understated the health check.** It also raised a 500 on any degraded
  dependency, flattening "Chroma is down" and "this endpoint is broken" into
  the same response. Now reports per-dependency status at 200.
- **§9.2's "`llm.py`: 2 lines" estimate was the only materially wrong sizing
  in the plan.** Every other Phase-0 estimate held.

### Phase 1 — typed tool args — **DONE**

7. ~~`args_model` on `Tool`; one model per tool~~ — `risk` deliberately NOT
   added; it is Phase 2's, and a field with no reader is dead schema
8. ~~Validate in `_execute_subtask`; delete the `json.loads` → `{}` fallback~~
9. ~~JSON Schema into the prompt alongside the prose description~~

*Exit criteria met: no unvalidated kwargs reach a tool — one gate
(`tools/base.py`'s `validated_kwargs`) covers both execution seams, all 15
registered tools expose a schema, and the battery is 6/7 both before and after
(the one failure, `reasoning_no_tool`, reproduces identically on a clean tree:
gpt-4o-mini reaches for `code_execution` to compute 15% of 240).*

**Corrections found while implementing:**

- **The `TypeError` catch was kept, not deleted.** It is no longer reachable
  for a validated local tool, but it is still the only guard for the two
  `**kwargs` tools whose contract isn't a local pydantic model: an MCP tool
  trusting a third party's schema, and `generate_tweet`'s deliberate
  `extra="allow"`. Deleting it would trade a failed `ToolResult` for a
  crashed graph run in exactly the case the audit didn't consider.
- **§5.1 understated the plumbing hole.** The injected kwargs were
  `setdefault`, so the model's value won whenever it supplied one — including
  `task_id` (which task's sandbox `file_io` writes to). Only `user_id` was
  assigned unconditionally. Ordering, not typing, is what fixes this: the args
  model has no field for them, and `_injected_kwargs` is applied *after*
  validation.
- **A live defect fell out of writing the contracts.** `kwargs.setdefault(
  "task_id", ...)` was applied to all five Google tools, but only
  `google_photos_pick` has a `task_id` parameter — so every `gmail_*` and
  `google_drive_*` call has been failing on "invalid arguments" since the
  Photos picker landed (`e8c619a`). The new
  `run signature == args model ∪ injected keys` test is what catches this
  class.
- **There are two execution seams, not one.** `delegate_subagent` has its own
  loop with its own `json.loads(...) → {}` and its own, shorter plumbing table
  — which knew nothing about `user_id`, so a sub-agent calling `gmail_search`
  could never work. Both now share `validated_kwargs` and `_injected_kwargs`.
- **MCP tools need no pydantic models.** They already advertise JSON Schema, so
  `MCPTool` overrides `validate_args` and checks against it with `jsonschema`
  (already a hard litellm dependency). Two limits stated in the code: an empty
  advertised schema gets no local check, and `additionalProperties` is the
  server's call, not ours.
- **Model-level docstrings must be stripped from the generated schema.**
  Pydantic puts an args model's class docstring in the JSON Schema
  `description`, which would have put maintainer rationale into every prompt —
  the exact failure `mcp_servers/company_internal.py` warns about.

**Open defects found while verifying Phase 1** — none caused by it, none fixed
yet. Measured 2026-08-25 over 40 probe runs (10× each case, on `3c98aef` and on
`6ef7cab` in a detached worktree), so treat these numbers as established rather
than re-spending the API calls.

| | `6ef7cab` | `3c98aef` | first tool proposed | rejected calls |
|---|---|---|---|---|
| `reasoning_no_tool` | 0/10 | 0/10 | `code_execution` 10/10 both | 0 |
| `web_search_recent_fact` | 10/10 | 9/10 | `web_search` 9, `knowledge_search` 1 | 0 |

`rejected_calls == 0` across all 40 runs is the finding that clears Phase 1:
validation never turned a previously-accepted call into a rejected one. Its
only measurable effect is prompt size, +21–31% tokens from the schema block —
cached prefix, so cost rather than correctness.

1. **`SERVICE_TOKEN` is unset in `.env` and in the containers**, so
   `knowledge_search` can never succeed — yet it registers unconditionally and
   its description tells the model to try it *before* `web_search`. When the
   model follows that advice the tool errors twice, the reviewer escalates, and
   the case dies. This breaks this repo's own rule that an unusable tool
   removes itself from the advertised set (`code_execution` and the Google
   tools already obey it). Fix: gate registration on `settings.service_token`,
   or set the token.
2. **The agent reaches for `code_execution` to do arithmetic** — "15% of 240"
   proposes `print(240 * 0.15)` on 10/10 runs of both commits, which gates into
   an approval escalation. `reasoning_no_tool` is therefore a deterministic
   failure reporting a real behaviour, not a flaky case. Fix: one sentence in
   that tool's description, then re-measure.
3. **`check_case` reports both of the above as the same thing.** Its escalation
   branch runs before every other check, so both surfaced as "escalated, but
   this request should have been handled", masking a Docker approval gate in
   one case and a misconfigured tool in the other. Fix: include the escalation
   kind and the last tool error in the verdict reason. No pass/fail change.

Tier 2 also sets no `seed` and no `temperature` at either `litellm.completion`
call site, so "every stochastic run must pass" is not a property this gate can
hold. Prefer deterministic subchecks (tool called, args carry the constraint,
approval fired, step ceiling) as hard pass/fail, with a measured pass-rate for
model-choice cases. Not changed yet.

### Phase 2 — policy — **DONE**

10. ~~`policy.py`; route the approval gate through `decide()`; delete
    `requires_approval`/`needs_approval`~~
11. ~~Record the decision + reason on the audit chain~~
12. ~~`tests/test_policy.py` as a tier-1 battery~~

*Exit criteria met: gating a tool is a rule in one function in one file.
`requires_approval` and `needs_approval` are deleted; all 15 registered tools
declare `action_type` + `risk`; `policy.decide()` is the only thing that
decides whether a call runs, and it is called at **both** execution seams.
Tier 1 is 49 new pure tests (free: verified passing with Postgres, Redis and
every API key unreachable), plus 19 DB-backed flow tests. Full suite 447
passed / 2 failed, both failures reproducing identically on `3c98aef`.*

**Corrections found while implementing:**

- **§5.2 understated the hole: the approval gate covered one of the two
  execution seams.** `_execute_subtask` checked `needs_approval`;
  `delegate_subagent`'s inner loop checked *nothing*, so a sub-agent that
  proposed `code_execution` simply ran it — approval was bypassable by
  delegating. Phase 1 closed the *validation* hole in both seams and this one
  was left behind in one. Both now call `policy.decide()`. A sub-agent cannot
  pause for a human (it runs synchronously inside one tool call), so its
  enforcement is **refuse**, not escalate; the parent agent can still propose
  the action directly and get a real approval.
- **`Risk` needs no ordering.** §8.2 sketched `tool.risk >= Risk.WRITE`. Every
  rule actually written is an equality or a membership test, so `Risk` is a
  plain `str` enum. Add ordering when Phase 3's ledger is the first caller
  that needs it — not before, and with tests pinning it.
- **The audit's own §8.3 said "approval expiry, step-up auth: add when the
  first genuinely irreversible tool exists (Phase 5)". Expiry landed now
  anyway, because it turned out to cost almost nothing**: `Escalation.created_at`
  already existed, and the resume path already funnelled through one function.
  It is ~15 lines in `nodes._approval_refusal` plus one setting. Step-up auth
  did *not* land — see the MFA note below.
- **`system_api.py` was a reader of `requires_approval` nobody had listed.**
  The `/system/tools` response fed a badge in the web UI. It now derives the
  same boolean from `policy.decide(tool, {})`, so the API keeps its shape while
  the static attribute goes away.
- **`PolicyDecision` needs `action_type` and `risk`, not just decision +
  reason.** The brief suggested the minimal pair. But an argument-dependent
  rule *raises* a call's classification above its tool's baseline, and §13's
  audit record has to show the effective values — re-deriving them from the
  tool at the audit seam would record the baseline and silently lose the
  reason the call was gated.
- **A fail-closed handler must not read the object it is failing closed
  about.** The first version of `decide()`'s exception path used
  `getattr(tool, "risk", default)` to report the classification. `getattr`
  with a default only swallows `AttributeError` — a property raising anything
  else throws straight out of the handler that exists to prevent exactly that.
  The fallback now uses constants.
- **MCP tools are the only inheritors of the fail-closed default, and gating
  all of them was too blunt.** A third-party server's effects are unknown, so
  `EXTERNAL_WRITE`/`HIGH` is right by default. But it would also have gated
  this repo's own demo server (`get_current_time`, `roll_dice`,
  `lookup_office` — all pure reads). Resolved with an optional per-server
  declaration in the `mcp_servers` config entry that already exists; a server
  that declares nothing stays gated.
- **`db_query` stays ALLOW, deliberately.** It is MEDIUM risk — arbitrary SQL
  on the app's own engine — but its boundary is the Phase-0 three-layer guard,
  not the gate. Gating it would put a human in front of every metrics lookup,
  which is how humans learn to approve without reading. Policy weakens nothing
  there; there is a regression test asserting the guard still refuses.

**Policy debt, stated rather than hidden:**

1. **Step-up auth / MFA is not implemented, and cannot be honestly faked.**
   `UserSession.mfa_satisfied_at` exists in the schema and is written by
   *nothing* — `grep -rn mfa src/` outside `db/models.py` returns no hits.
   There is no second factor anywhere in the auth flow (sign-in is Google
   OAuth, one hop), so "require recent MFA for this action" would be a check
   against a column that is always `NULL`: either always-deny, or a no-op dressed
   as a control. Phase 2 does not read the field. **Debt: the field exists but
   no usable step-up flow does.** It needs a real second factor first.
2. **A sub-agent cannot escalate.** It refuses gated actions instead. Making
   it escalate means suspending a running tool call mid-flight and resuming
   into it, which is a durable-execution change (Phase 3), not a policy one.
3. **No per-user, per-org or per-environment policy.** `decide()` accepts
   `user_id` and no rule reads it. There is no environment concept in the app
   at all, so `decide()` deliberately has no `environment` parameter — adding
   the config field so that policy could accept it would be inventing the
   requirement to satisfy the abstraction.
4. **Nobody decides who may approve what.** Any owner of a task can approve
   any escalation on it. Unchanged from before Phase 2, still §5.2's gap.
5. **The approval fingerprint is drift resistance, not tamper-proofing.** It
   lives in the same JSONB dict it protects, so anything with write access to
   the row could update both halves. It catches the failure that actually
   threatens this codebase — a future code path changing the arguments without
   re-evaluating policy — not a database attacker.
6. **`_effective()` keys argument-dependent classification off `tool.name`.**
   One entry (`file_io`) today. That is a string switch, and it is the right
   shape while there is one: it keeps the whole policy readable in one place,
   which is the §13 auditability goal. Revisit if it reaches ~5 entries.

### Phase 3 — durable execution

13. Migration: `ToolCall.args_hash`, `ToolCall.task_id`, `Task.plan`
14. Ledger lookup before executing any `Risk.WRITE`+ tool; reuse
    `deadcalls._signature` for the hash
15. Plan moves from TraceSpan to `Task.plan` (§4.2 / R2)
16. `recovery.py` relies on the ledger rather than documenting the hole
17. Collapse §4.4's two prompt paths into one

*Exit: kill the worker mid-tool-call, resume, and the side effect happens
exactly once.*

### Phase 4 — verification

18. `verify.py`: checker table, LLM reviewer as fallback
19. Per-step `criteria` written by the planner into `Task.plan`
20. Goal-level check before `synthesize`
21. Fix R3: include failed/escalated steps in the next decision's context

*Exit: the requested lifecycle exists as distinct, testable stages.*

### Phase 5 — the first real action

22. One irreversible tool (`send_email` over the existing Gmail grant) as the
    **proof** Phases 1–4 work
23. Eval cases: must be gated, idempotent across a crash, verified after
    sending, auditable end to end
24. `docs/SECURITY.md`

*Do not start Phase 5 before Phase 3 is done.*

### Phase 6 — opportunistic

25. Collapse `src/rag` into an imported package, or give it real per-user
    tenancy. One or the other; the current middle is the worst option
26. Delete the Streamlit dashboards
27. Parallel steps (G12) — and *then* the `PlanStep` table, if it's needed

---

## Relationship to ROADMAP.md

`ROADMAP.md` optimises for *legibility*; this document optimises for *safe
action*. Overlaps:

- **G5** (retrieval gate + mid-loop memory) — unchanged, slots into Phase 4
- **G1** (skills) — a skill is a `Task.plan` template once Phase 3 lands
- **G12** (parallel steps) — safe after Phase 2 + 3, and the trigger for the
  `PlanStep` table §8.3 defers
- **G7** (provider registry) — the `llm.py` change in Phase 0 is its
  prerequisite

The ROADMAP's blocked visual verification is orthogonal; none of the work above
is UI work.
