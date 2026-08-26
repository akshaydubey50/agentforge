# Phase 3 implementation note — durable execution

Written before any code, per the phase brief. Records what the execution path
actually does today, where the crash window is, and which existing mechanisms
already cover part of the requirement — so the change is a re-route and two
columns rather than a new subsystem.

## 1. What `ToolCall` already stores

```python
class ToolCall(SQLModel, table=True):
    id: str
    subtask_id: str      # FK -> subtask.id.  No task_id: ownership is a join hop
    tool_name: str
    input: dict          # JSONB -- the VALIDATED kwargs, injected plumbing included
    output: dict         # JSONB -- result.output on success, {"error": ...} on failure
    success: bool        # NOT NULL
    latency_ms: int
    created_at: datetime
```

Four properties matter for Phase 3:

1. **`input` is already the exact validated kwargs.** Phase 1 made this true:
   `_run_tool_call` receives the output of `validated_kwargs`, injected values
   included, and stores it verbatim. So the row already carries everything an
   effect identity needs — it just carries it as an unindexed JSONB blob that
   no query can match on.
2. **The row is written *after* the tool returns**, in one transaction, in
   `_run_tool_call` (graph/nodes.py:582). There is no row while the tool runs.
3. **`success` is a two-valued NOT NULL boolean**, and it only exists once the
   outcome is known. There is no state for "we called it and never found out".
4. **Sub-agent tool calls write no `ToolCall` row at all.**
   `delegate_subagent.run()` calls `registry.get(tool_name).run(**kwargs)`
   directly (tools/delegate_subagent.py:170). Its calls appear only as
   `subagent_tool_call` TraceSpans.

Consumers, so a schema change is not a surprise later:
`eval/gate.py::_run_record` (tool name + args, ordered), `eval/runner.py`
(outputs, for grounding), `main.py /v1/analytics` (`by_tool` success counts),
`system_api.py` (`COUNT` + `SUM(success == False)`), and three tests.

## 2. Current execution lifecycle

Main loop, one step:

```
agent_step_node
  └─ _execute_subtask
       1. subtask.attempt_count += 1                          [COMMIT]
       2. tool selection (LLM)  -> ToolChoice
       3. validated_kwargs(...)                                Phase 1
       4. policy.decide(...)                                   Phase 2
            DENY             -> _rejected_call, nothing runs
            REQUIRE_APPROVAL -> Escalation(tool_approval), returns None
            ALLOW            -> continue
       5. deadcalls.is_dead(task, tool, args)                  Redis, failures only
       6. _run_tool_call
            a. span(...) __enter__      INSERT TraceSpan       [COMMIT]
            b. registry.get(name).run(**kwargs)   <-- THE EFFECT
            c. span __exit__            UPDATE TraceSpan       [COMMIT]
            d. INSERT ToolCall(success=...)                    [COMMIT]
       7. deadcalls.record_failure / record_success            Redis
       8. subtask.output = ...                                 [COMMIT]
```

Approval resume: `apply_escalation_decision` → `run_gated_tool_call` →
`_approval_refusal` (expiry, fingerprint, re-decide) → the **same**
`_run_tool_call`, steps 6a–6d, with the stored kwargs.

Sub-agent loop: steps 3 and 4 are present (Phase 1 and Phase 2 closed both
seams), then `tool.run(**kwargs)` bare — no span-wrapped recording, no
`ToolCall` row, no dead-call ledger.

## 3. The crash window, exactly

```
t0   INSERT trace_span (status=ok, output={}, ended_at=NULL)      COMMITTED
t1   tool.run(...)  ->  file written / container run / API call    THE EFFECT
t2   <<< SIGKILL, OOM-kill, container restart, celery hard limit >>>
t3   -- nothing further is written --
```

After `t2`, Postgres contains **no evidence the effect happened**. The
`TraceSpan` row from `t0` survives with `ended_at IS NULL`, and nothing reads
it for this purpose. On the next invocation:

```
run_task(task_id)
  └─ reconcile_orphaned_subtasks -> the subtask is RUNNING, so mark it FAILED
  └─ agent_step_node -> the model sees a failed step, re-proposes the same call
  └─ _execute_subtask -> validation passes, policy passes, deadcalls says
                         nothing (only failures are recorded, and none was)
  └─ _run_tool_call -> THE EFFECT HAPPENS A SECOND TIME
```

`recovery.py`'s own docstring already states this ("a re-run can re-execute a
side-effecting tool call that already happened before the crash"), and
`ARCHITECTURE_AUDIT.md` §5.3 states it again. It is not a hypothetical.

Two narrower variants of the same window:

- `t1` succeeds, `t3`'s `INSERT ToolCall` fails (Postgres blip, pool
  exhausted). `_run_tool_call` raises; the effect happened; nothing recorded.
- Celery redelivery. `task_acks_late=True` with
  `task_reject_on_worker_lost=False`: a *killed* worker's message is not
  redelivered, but `recover_stranded_tasks()` re-enqueues the task on the next
  worker boot, which lands in exactly the resume path above.

And the approval path has its own: `run_gated_tool_call` re-checks expiry,
fingerprint and policy — three checks that the call is still *permitted* — and
then executes unconditionally. It never asks whether it already *ran*. Approve,
crash mid-tool, worker reboots, task resumes, model re-proposes: second effect.

## 4. What identity/hash helpers already exist

Two, and **neither is reusable as-is**. Both are correct for their own job.

| Helper | Covers | Why it is wrong for effect identity |
|---|---|---|
| `deadcalls._signature(tool, kwargs)` | `sha`-free canonical `tool:{json}` over kwargs **minus** `{task_id, subtask_id, user_id, depth, goal}` | Strips exactly the two fields an effect is scoped by. `user_id` decides *whose Gmail*; `task_id` decides *which sandbox*. Two users making the identical `gmail_read` would share one key. Its own docstring says why it strips them — a repeat is the same *dead end* regardless of which run proposed it. Right for loop control, wrong here. |
| `policy.args_fingerprint(tool, kwargs)` | SHA-256 over **all** kwargs, injected plumbing included | Keeps `subtask_id` and `depth`. After a crash, `reconcile_orphaned_subtasks` fails the subtask and the loop authors a **new** one — so the same logical effect gets a new `subtask_id` and a different fingerprint. It is bound to *one approval of one call in one step*, which is what an approval must be. Right for approval binding, wrong here. |

So Phase 3 adds a third, one function long, sharing `deadcalls._signature`'s
strip-then-canonicalise shape but with a different strip set and an explicit
identity envelope:

```python
_VOLATILE = {"subtask_id", "depth"}          # differ between two runs of ONE effect

def effect_key(tool_name, kwargs, *, task_id, user_id=None) -> str:
    meaningful = {k: v for k, v in kwargs.items() if k not in _VOLATILE}
    return sha256(json.dumps(
        {"task": task_id, "user": user_id, "tool": tool_name, "args": meaningful},
        sort_keys=True, default=str,
    ))
```

`task_id` and `user_id` are passed explicitly rather than read out of `kwargs`,
because only some tools get them injected (`web_search` gets neither) and
identity must be uniform across tools. `sort_keys` + `default=str` for the same
two reasons the other two helpers give.

## 5. What is missing

| Requirement | Present today | Missing |
|---|---|---|
| Stable effect identity | `input` JSONB holds the args | no key, no index, nothing to query on |
| "Did this already succeed?" | — | no pre-execution lookup anywhere |
| Distinguish never-ran / in-flight / failed / succeeded | `success` bool, written after the fact | the in-flight state, i.e. a row that exists *before* the effect |
| Per-tool retry semantics | — | nothing says whether repeating a tool is safe |
| Failure classification | `llm.py` does it for **model** calls, by exception type | tools report failure as a `ToolResult.error` **string**; nothing classifies it |
| Bounded retry + backoff | `llm._attempt_series` (LLM only); `max_subtask_retries` (LLM-driven re-proposal) | no deterministic retry of the *same* call |
| Timeouts | every tool already has one (§9 below) | one gap: MCP `call_tool` |
| Crash routing by safety | `reconcile_orphaned_subtasks` marks subtasks FAILED | it never looks at tool calls |

## 6. Smallest viable design

One new module, two changed columns, one migration, one class attribute,
three settings. No new tables.

### 6.1 `ToolCall` — two column changes

```python
effect_key: str | None = Field(default=None, index=True)   # NEW, nullable
success: bool | None                                        # was bool NOT NULL
```

`success` becomes tri-state, and the third state is the honest one:

```
True   the tool ran and reported success
False  the tool ran and reported failure  (or recovery resolved an ambiguous row)
NULL   AMBIGUOUS -- the row was written before the call and never resolved
```

A nullable boolean rather than a second `status` column, because two columns
that can disagree is worse than one column with a documented NULL — and this
repo already uses exactly that convention deliberately (`LlmCall.cached_tokens`:
"NULL means *not recorded*, not *none*"). No `ToolCall.task_id` column is
needed: `task_id` is inside the hash, so a lookup by `effect_key` alone is
already task-scoped.

### 6.2 `execution.py` — the new module

Mirrors `policy.py`'s role for Phase 2: the deterministic answer to a
different question. `policy` answers *may this run?*; `execution` answers
*has it already run, and is running it again safe?*

```
ExecutionSafety  IDEMPOTENT | VERIFY_BEFORE_RETRY | NON_RETRYABLE_SIDE_EFFECT
FailureKind      TRANSIENT | RATE_LIMIT | TIMEOUT | AUTH | PERMANENT | UNKNOWN
effect_key(...)                      -> str
classify_failure(error_text)         -> FailureKind
run_with_retry(tool, kwargs)         -> (ToolResult, attempts, FailureKind|None)
execute_tool(tool, kwargs, ...)      -> ToolOutcome    # ledger + dedupe + retry
resolve_ambiguous_calls(subtask_ids) -> int            # called by recovery
```

**`IDEMPOTENCY_KEY_SUPPORTED` is deliberately not a mode.** No registered tool
can carry an external idempotency key — there is no external write tool at all
yet (§7 below: every tool is READ or LOCAL_WRITE). Adding the mode would mean
inventing a key-injection protocol for zero callers, and worse, a future tool
author could declare it and receive no protection. It is the same call
`policy.py` already made about a CRITICAL escape hatch and an `ActionType.PURE`
category: build it alongside the first tool that justifies it, which is
Phase 5's `send_email`. Until then the honest ceiling is stated in §10.

### 6.3 Where the two decisions bind

**Dedupe scope is the Phase 2 `action_type`, not a new field.** The ledger's
"already succeeded → reuse" check applies only when the effective `action_type`
is *not* `READ`. That is not a shortcut; deduping reads would be a bug:

> `google_photos_pick` returns `_awaiting_human`, the task pauses, a human
> picks, the task resumes and calls the **identical** tool with the **identical**
> arguments — and must reach the tool, because the second call is what collects
> what the human selected. It is a `READ`. Deduping it would hand back the
> stale first result forever.

Re-searching after a write and polling a status are the same shape — which is
precisely the reasoning `deadcalls.py` already gives for never blocking a
repeated success. Reads therefore keep today's single post-hoc `ToolCall`
INSERT with `effect_key = NULL`; effects get the full lifecycle.

**Retry scope is the new `ExecutionSafety`.** Only `IDEMPOTENT` tools are
retried automatically, and only for `TRANSIENT | RATE_LIMIT | TIMEOUT`:

```
IDEMPOTENT                 + TRANSIENT/RATE_LIMIT/TIMEOUT -> retry, bounded, backoff
IDEMPOTENT                 + AUTH/PERMANENT/UNKNOWN       -> no retry
VERIFY_BEFORE_RETRY        + anything                     -> no automatic retry
NON_RETRYABLE_SIDE_EFFECT  + anything                     -> no automatic retry
```

Collapsing the matrix this way is deliberate: *if repeating is not known-safe,
do not repeat it automatically.* A non-idempotent tool's rate-limit failure is
not swallowed — it returns to the agent loop, which can re-propose and get a
fresh policy evaluation. That is a decision, not a blind repeat.

`UNKNOWN` is not retried, per the brief's "retry only known-retryable
failures". Validation failures and policy denials are absent from `FailureKind`
because they never reach execution — they are refused at their own seams
(`_rejected_call`) and no code path can retry them.

### 6.4 Write ordering

```
effectful call:                          read call (unchanged):
  compute effect_key
  pg_advisory_xact_lock(hash(effect_key))
  SELECT ... WHERE effect_key = ?
    success=True  -> reuse, no execution
    success=NULL  -> in-flight/ambiguous: refuse, no execution
  INSERT ToolCall(success=NULL)  COMMIT
  run_with_retry(...)                      run_with_retry(...)
  UPDATE ToolCall(success=...)   COMMIT    INSERT ToolCall(success=...)  COMMIT
```

The advisory lock is transaction-scoped and held only around the claim:
lookup, decision, and insertion of the in-flight row. The commit both makes
the in-flight row durable before the tool runs and releases the lock. A second
worker racing the same effect therefore sees the `success IS NULL` row and
does not execute the tool concurrently.

A crash between the INSERT and the UPDATE leaves `success IS NULL`, which is
the whole point: after the crash the system can tell *never executed* (no row)
from *maybe executed* (a NULL row) from *definitely failed* (False) from
*definitely succeeded* (True).

### 6.5 Recovery routing

`reconcile_orphaned_subtasks` already runs first on every invocation and
already knows which subtasks were stranded. It gains one step: resolve the
ambiguous `ToolCall` rows under them, by the tool's safety mode.

```
success=True                                  -> untouched; dedupe will reuse it
success=False + retryable + safe              -> untouched; re-proposal executes
success=NULL + IDEMPOTENT                     -> resolved to False ("outcome unknown,
                                                 safe to repeat") -> re-execution allowed
success=NULL + VERIFY_BEFORE_RETRY            -> left NULL -> a matching call is
                                                 REFUSED with "verify first"
success=NULL + NON_RETRYABLE_SIDE_EFFECT      -> left NULL -> a matching call is
                                                 REFUSED, agent must report it
```

The unresolved NULL row *is* the block: `execute_tool`'s pre-execution lookup
treats it as a stop. No new escalation kind — the refusal is a step failure the
existing unproductive-streak/budget machinery already escalates on.

### 6.6 `delegate_subagent`

Uses `run_with_retry` directly, not `execute_tool`. It gets identical retry
classification, backoff and bounds. It gets no ledger row and no dedupe, and
that is not a gap: a sub-agent refuses everything policy does not `ALLOW`
(Phase 2), `LOCAL_WRITE` and above are `REQUIRE_APPROVAL`, so **every call a
sub-agent can execute is a READ** — which is exactly the class that is not
deduped and has no effect to recover. Phase 2's refusal is what makes this
true; if that ever loosens, this must change with it.

### 6.7 Deliberately not built

- No `Action` / `ActionAttempt` / effect / ledger tables. `ToolCall` already
  holds tool, args and outcome; it was missing an identity and a third state.
- No attempt column: attempts are `COUNT(*)` over rows sharing an `effect_key`,
  and the in-call retry count rides on the span/observation.
- No new Celery queue and no requeue-based retry. `llm.py` already establishes
  in-process bounded retry with equal jitter as this repo's pattern, and the
  delays are ~0.5–8s, far inside the worker's own wall clock.
- No process-killing timeout infrastructure. Every tool bounds itself (§9);
  MCP discovery and calls use `asyncio.wait_for`.
- No step-up/MFA. `mfa_satisfied_at` is still populated by nothing (Phase 2's
  finding stands).
- No change to the approval design. Phase 3 adds a ledger check *before*
  `_run_tool_call` executes, and touches nothing else in that path.

## 7. Execution-safety classification (from reading each implementation)

| Tool | Phase 2 | Execution safety | Why |
|---|---|---|---|
| `web_search` | READ/LOW | `IDEMPOTENT` | Stateless query against a search API or an HTML scrape. Nothing changes. |
| `knowledge_search` | READ/LOW | `IDEMPOTENT` | HTTP POST, but a read: `rag-api /v1/ask` retrieves and generates, stores nothing. |
| `generate_tweet` | READ/LOW | `IDEMPOTENT` | Pure text generation. Repeating costs LLM spend, which `max_task_cost_usd` already bounds; it produces no effect. |
| `file_io` (read/list) | READ/LOW | `IDEMPOTENT` | Reads a sandboxed path. |
| `file_io` (write) | LOCAL_WRITE/MEDIUM | `IDEMPOTENT` | `_write` is `mkdir(parents, exist_ok) + write_text`, which truncates. Same path + same content ⇒ same final state, genuinely. Deduped anyway because it is not a READ — the dedupe is what makes the *result* stable across a resume, the idempotency is what makes a retry safe. |
| `db_query` | READ/MEDIUM | `IDEMPOTENT` | SELECT-only, enforced by statement shape + EXPLAIN-resolved allowlist + a read-only role (Phase 0). |
| `gmail_search` / `gmail_read` | READ/MEDIUM | `IDEMPOTENT` | Gmail `messages.list` / `messages.get`. Neither mutates labels or read state. |
| `google_drive_search` / `google_drive_read` | READ/MEDIUM | `IDEMPOTENT` | `files.list` / `files.get` + export. Read-only scopes. |
| `google_photos_pick` | READ/MEDIUM | `IDEMPOTENT` | It creates a picker session on Google's side, but stores the session id in the task's short-term memory *before* returning, and a second call reuses it rather than opening a second picker. The tool's own docstring calls this out: "IDEMPOTENT ON THE SESSION". |
| `code_execution` | LOCAL_WRITE/HIGH | `NON_RETRYABLE_SIDE_EFFECT` | Conservative, and worth stating why precisely: the container is `detach=True, network_disabled=True` with **no volume mount**, so it cannot touch the task workspace and cannot reach any external service — the durable effect is nil. The classification is not about filesystem mutation; it is that arbitrary code approved once by a human must not be auto-replayed after an ambiguous outcome. |
| `delegate_subagent` | READ/MEDIUM | `NON_RETRYABLE_SIDE_EFFECT` | A whole nested loop with its own LLM spend and its own tool calls. Replaying it re-runs everything it already did. |
| MCP — declared server | per config | per config | `mcp_servers` entries may declare `"execution_safety": "idempotent"`, through the same `_declared()` helper that already parses `action_type`/`risk`. `company_internal` (this repo's own read-only demo server) declares it. |
| MCP — undeclared server | EXTERNAL_WRITE/HIGH (fail-closed) | `NON_RETRYABLE_SIDE_EFFECT` (fail-closed) | Third-party code this repo has never seen. It is already gated behind human approval; it must also never be auto-retried after an ambiguous result. |
| Any tool that forgets to declare | EXTERNAL_WRITE/HIGH | `NON_RETRYABLE_SIDE_EFFECT` | The class default is the conservative one, same fail-closed reasoning as `Tool.action_type`. |

## 8. Failure classification, honestly

`llm.py` classifies by **exception type**, because litellm raises typed
exceptions. Tools do not: every first-party tool catches its own exceptions and
returns `ToolResult(success=False, error="<string>")`. So classification here
is a deterministic match over that string, and it is deliberately narrow —
anything it does not recognise is `UNKNOWN`, and `UNKNOWN` is never retried.
That keeps the brittleness `deadcalls.py` warns about ("guessing permanence
from an error string is brittle") on the safe side of the decision: a
misclassification costs one un-retried transient failure, never an extra
effect.

Exceptions that escape a tool's `run()` still propagate exactly as today (only
`TypeError` is caught, as before). Phase 3 does not widen that catch: swallowing
an unexpected exception into a retry loop would hide a bug behind a retry,
which is the rule `llm.py` already states.

## 9. Timeouts — covered

| Category | Bound | Where |
|---|---|---|
| HTTP (Tavily / DDG / Gmail / Drive / Photos / knowledge) | 10–60s per request | `timeout=` on every `httpx` call |
| Database (`db_query`) | `SET LOCAL statement_timeout` | tools/db_query.py:174 |
| Filesystem (`file_io`) | n/a — local, bounded by the sandbox | — |
| Code execution | `container.wait(timeout=)` **plus an explicit `container.kill()`**, because that timeout is a client-side HTTP read timeout | tools/code_execution.py:122 |
| MCP connect/initialize | `asyncio.wait_for(..., CONNECT_TIMEOUT_S)` | tools/mcp_tool.py:289 |
| MCP discovery `list_tools` | `asyncio.wait_for(..., DISCOVERY_TIMEOUT_S)` | tools/mcp_tool.py |
| MCP `call_tool` | `asyncio.wait_for(..., CALL_TIMEOUT_S)` | tools/mcp_tool.py |
| Whole task | Celery soft/hard limits | worker.py |

Discovery and runtime calls are both bounded: a server that connects but never
lists tools degrades through the existing "server unavailable, skip its tools"
registry path, and a server that accepts a tool call but never answers returns
a failed `ToolResult`.

## 10. What this can and cannot promise

Stated in the vocabulary the brief asks for, per category:

| Class | Guarantee | Which tools |
|---|---|---|
| **Safe repeat (idempotent)** | Repeating produces the same end state. Retry after a known failure or after recovery clears an ambiguous interrupted call is safe. | every READ tool, `file_io` write |
| **Deduplicated local execution** | A logical effect that already succeeded in this task is *not executed again*; the prior result is reused. Concurrent claims for the same effect are serialized before execution. Durable (Postgres), survives a crash, a resume and a worker change. | `file_io` write today; every future non-READ tool |
| **External idempotency-key protection** | **Not implemented.** No tool can carry a key. | none |
| **At-least-once** | What an `IDEMPOTENT` tool gets after recovery marks an ambiguous interrupted call as safe to repeat: it may run twice, and that is safe by classification. | READ tools, `file_io` write |
| **At-most-once** | What a `VERIFY_BEFORE_RETRY` / `NON_RETRYABLE_SIDE_EFFECT` tool gets: after an ambiguous outcome it is refused rather than repeated. It may therefore *not* have happened at all. | `code_execution`, `delegate_subagent`, undeclared MCP |
| **Ambiguous external side effect** | The genuinely unresolvable case: the effect may or may not have landed and this system cannot tell. Recorded as `success IS NULL`, surfaced, never guessed at. | any external write, once one exists |

**This is not exactly-once, and Phase 3 must not be described as such.** A
database ledger cannot make it exactly-once: the ordering is

```
INSERT in-flight  COMMIT   ->   external effect happens   ->   << crash >>   ->   nothing
```

and no amount of local bookkeeping recovers what happened between the second
and third arrow. Exactly-once needs the *external* system to deduplicate — a
Stripe-style idempotency key, or a verification read that can prove whether the
effect landed. Both belong to the first tool that has one, not to this phase.
What Phase 3 does deliver is that the system never *blindly* repeats an effect,
and that it always knows which of the four states it is in.
