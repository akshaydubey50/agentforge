# Phase 4 implementation note - verification after safe execution

Written before code, per the phase brief. This phase adds the missing question
between "the tool returned" and "the step/task is done":

```text
Did the observed result actually satisfy the current step or goal?
```

It does not replace Phase 1 validation, Phase 2 policy, Phase 3 execution
safety, or the existing reviewer. It puts deterministic verification before
semantic review and before final synthesis.

## What the current reviewer actually checks

`nodes._review_subtask()` is an LLM review of one subtask. It receives the
subtask description, the assigned tool name, a boolean saying whether the tool
call succeeded, and the subtask output text. It returns `ReviewOutput`:

```python
score: int
verdict: "pass" | "reject" | "escalate"
feedback: str
```

`pass` marks the subtask `DONE`. `reject` retries the same subtask while
`attempt_count < settings.max_subtask_retries`. Otherwise the subtask becomes
`ESCALATED` and an existing `Escalation(kind="review")` is created.

The reviewer is useful semantic judgment. It can tell whether a summary
actually answers a subtask, whether reasoning over retrieved data is coherent,
or whether a failed tool result was handled honestly. It is not a state
verifier.

## What information it receives

The reviewer sees:

- `subtask.description`
- `subtask.assigned_tool` or `"none"`
- `tool_success` from `_execute_subtask()`
- `subtask.output`

It does not receive the raw `ToolCall` row, validated tool arguments, the
Phase 3 `ToolOutcome`, filesystem state, database state beyond whatever the
tool printed, or the whole goal. It is also explicitly told that a successful
tool result is ground truth and that it should not independently re-verify the
tool's data source.

## What it misses

The current path treats "tool returned success" and "step objective is true" as
too close together. It misses:

- deterministic postconditions such as "the file exists" or "the file content
  matches the requested content"
- structure checks such as "this search result has a results array"
- explicit count checks such as "at least 5 results"
- tool-success-but-objective-unmet cases, for example a search tool returning
  an empty result for a step that needed internal evidence
- unsafe ambiguous execution refusals that should route to a human rather than
  a normal semantic retry
- goal-level completion before final synthesis

The audit's R3 finding is still true after Phase 3: `_gather_prior_context()`
loads only `SubtaskStatus.DONE`. Failed and escalated subtasks are stored, but
the next `agent_step_node` decision cannot see their output or why they failed
except through the small Redis dead-call list.

## What existing logic can be reused

- `ToolCall` is the durable observation source for tool name, validated input,
  output, success and ambiguous state.
- `execution.classify_failure()`, `FailureKind`, and tool
  `execution_safety` already classify retryable vs permanent tool failures.
- `deadcalls.record_failure()`, `deadcalls.describe()` and the unproductive
  streak already stop repeated dead ends.
- `_review_subtask()` remains the semantic fallback when deterministic checks
  cannot settle the step.
- `_create_escalation()` and existing escalation kinds can carry verification
  failures to a human. A small `verification` kind is enough when the failure
  is not a policy approval or reviewer rejection.
- Trace spans already give the right observability seam.
- The current plan is the latest `TraceSpan(span_type="plan")`; replanning can
  stay as an `agent_step_node` decision, not a new planner or table.
- `eval.grounding.check_grounding()` is an example of the right discipline:
  deterministic signal first, "uncertain" instead of overclaiming, model
  judgment only when needed.

## Which verification checks can be deterministic

Deterministic checks should use evidence the system already has:

- `file_io` write: resolve the task workspace path, check the file exists, and
  when content is present in the validated kwargs, check the file content.
- `file_io` read/list: check the result has `content` or `files`.
- Search/read tools: check successful output has the expected top-level shape;
  only require non-empty results when success criteria explicitly say so.
- `db_query`: check `columns` and `rows` are lists; apply result-count
  criteria only when present.
- `code_execution`: check `stdout`, `stderr`, and `exit_code` shape; apply
  `exit_code == N` or output containment only when criteria say so.
- Phase 3 ambiguous execution refusal: route to human, because the system has
  explicitly recorded that it cannot safely know whether the effect landed.
- Goal file creation: if the user goal names a workspace filename, final
  synthesis should not run until that file exists.

Empty search or DB results are not a failure by themselves. They become a
verification failure only when the step's success criteria require results.

## Which checks genuinely require LLM judgment

LLM judgment remains appropriate when the criterion is semantic:

- whether a written report is a good research summary
- whether retrieved evidence is relevant enough to answer the question
- whether a multi-step explanation satisfies the user's natural-language goal
- whether a pure reasoning step completed its task
- whether the final answer covers a broad request such as "summarize trends"

Those should use the existing reviewer for step-level semantics and a small
goal-completion check before synthesis. They should not become new agents or a
parallel planning topology.

## How current retry/replan behavior works

Current retry is local to one subtask:

```text
_execute_subtask()
_review_subtask()
  pass -> DONE -> next agent_step
  reject and attempts remain -> NEEDS_REVISION -> _execute_subtask() again
  reject exhausted/escalate -> ESCALATED -> human
```

Current replan is the normal next `agent_step_node` decision. The agent sees
the living plan, prior done subtasks, and the dead-call list, then may provide
`updated_plan` and choose the next action. There is no structured diff and no
Plan/PlanStep table.

Phase 4 should keep that split:

- RETRY means the same subtask/action still makes sense and the existing
  review retry loop can run it again.
- REPLAN means the current approach did not satisfy the objective; mark this
  step failed, expose why in prior context, and let `agent_step_node` choose a
  different approach.
- REQUIRE_HUMAN means deterministic code cannot safely establish the outcome
  or continue.

## Smallest viable Phase 4 design

1. Add one nullable field to `Subtask`:

```python
success_criteria: str | None
```

No Plan table, no PlanStep table, no dependency graph. `NextStepDecision` gets
the same optional field so the current subtask can carry a compact criterion
such as `file_exists: report.txt`, `content_matches`, `result_count >= 5`,
`exit_code == 0`, or `semantic`. Existing rows have null criteria and fall
back to existing review behavior.

2. Add one small module, `agentsys.verification`, with:

```python
VerificationRoute = PASS | REVIEW | RETRY | REPLAN | REQUIRE_HUMAN | FAIL

class VerificationResult(BaseModel):
    verified: bool
    reason: str
    retryable: bool = False
    needs_replan: bool = False
    needs_human: bool = False
    route: VerificationRoute
```

`REVIEW` means deterministic checks cannot decide and the existing reviewer
should run. It is not a new agent.

3. Step verification runs before the reviewer:

```text
execution observation
deterministic verification
  PASS            -> mark DONE without an LLM review when the criterion is fully checkable
  REVIEW          -> call existing _review_subtask()
  RETRY           -> existing NEEDS_REVISION loop when attempts remain
  REPLAN          -> mark FAILED, expose reason, return to agent_step_node
  REQUIRE_HUMAN   -> create existing escalation
  FAIL            -> fail/escalate safely rather than synthesize success
```

4. Failed-step context is fixed by changing `_gather_prior_context()` to show
recent non-DONE steps with their status and output, especially FAILED and
ESCALATED steps. That reuses `Subtask.output`, `deadcalls`, and the existing
context budget instead of duplicating state.

5. Goal verification runs when the agent proposes `finish`, before routing to
`synthesize`. Deterministic checks catch obvious misses such as a requested
file not existing or unresolved failed/escalated steps. Semantic goals use a
small LLM fallback only when deterministic checks cannot decide.

6. Synthesis consumes verified/completed evidence only. If goal verification
fails, the graph returns to `agent_step_node` for replan or escalates; it does
not mark the task complete just because the model chose `finish`.

## Deliberately not built

- no send-email, calendar, publishing, or destructive external actions
- no external idempotency-key support
- no compensation/saga
- no Plan/PlanStep tables
- no DAG planning or parallel execution
- no verifier class registry
- no new agent topology
- no frontend changes
