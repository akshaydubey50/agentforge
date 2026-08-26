# Goal-Execution System — AgentForge North Star

This document defines the core design target for AgentForge:

> **A goal-execution system that can take a user goal, break it into verifiable steps,
execute safely, adapt when reality changes, and prove what happened.**

---

## 1) What “goal execution” means here

A goal-execution agent is **not** just a chat loop with tools. It must satisfy all of:

1. **Goal understanding**
   - Extract the concrete objective, constraints, and success criteria.
2. **Executable decomposition**
   - Convert goal into bounded steps that can each be executed and verified.
3. **Closed-loop execution**
   - Decide → execute → observe → update plan, repeatedly.
4. **Deterministic boundaries**
   - LLM may propose; code validates, gates, and enforces.
5. **Safety + governance**
   - Risk-aware action policy, approvals where required, auditable decisions.
6. **Outcome verification**
   - Confirm end-state against explicit success criteria before claiming done.
7. **Resumability**
   - Survive crashes/human pauses and continue from durable state.

---

## 2) Lifecycle (target)

1. **Goal analysis**
   - Parse request into: objective, constraints, deliverables, risks, unknowns.
2. **Plan synthesis**
   - Create structured plan with step IDs, dependencies, expected evidence.
3. **Policy check**
   - Evaluate risk per proposed action (not just per tool class).
4. **Step execution loop**
   - Select next step, run tool call with validated arguments, capture result.
5. **Step verification**
   - Verify each step outcome (deterministic checks first; model judgment only when needed).
6. **Replan if needed**
   - If blocked/failed/new evidence, revise remaining plan explicitly.
7. **Goal verification**
   - Validate that all success criteria are met.
8. **Synthesis + memory update**
   - Produce final user-facing result and persist reusable lessons.

---

## 3) Design principles

- **LLM proposes, deterministic code disposes**.
- **Fail open for optional intelligence layers** (triage/retrieval/judge), fail closed for safety/policy.
- **No silent degradation**: rejected tool calls and policy denials are first-class trace events.
- **State != telemetry**: plan/execution state stored in durable task tables, not only spans.
- **Verification before completion**: "looks good" is insufficient.
- **Proof over prose**: every major claim must map to recorded evidence (tool call, output, check).

---

## 4) Current status vs goal-execution target

### Already strong

- Durable task/subtask state in Postgres.
- Human escalation workflow (approve/reject/take_over).
- Sequential adaptive loop (`agent_step`) with reviewer and retry paths.
- Audit/event traceability and resume model across worker boundaries.
- Typed tool argument validation gate (Phase 1).

### Gaps to close

1. **Structured plan state**
   - Plan currently behaves like text, not a first-class execution graph.
2. **Per-call policy engine**
   - Need risk rules based on action + arguments + actor/context.
3. **Durable tool-call idempotency/effects ledger**
   - Prevent duplicate side effects across crashes/retries.
4. **Explicit verification stage**
   - Need deterministic checkers for goal-level completion.
5. **Eval semantics for stochastic behavior**
   - Deterministic checks must gate; stochastic cases need measured stability policy.

---

## 5) Acceptance criteria for “goal-execution capable”

AgentForge should be considered goal-execution capable when:

1. For a representative task suite, each run emits:
   - structured goal spec,
   - structured plan,
   - step evidence,
   - goal-verification evidence.
2. Side-effecting calls are idempotent across retries/crashes.
3. Policy decisions are explainable and reproducible from stored context.
4. Completion claims can be independently checked from persisted records.
5. Release gate separates deterministic failures from stochastic variation.

---

## 6) Non-goals (for now)

- Maximum autonomous breadth (many tools) over reliability.
- Parallel execution complexity before deterministic governance is solid.
- Cosmetic benchmark wins without traceable, repeatable correctness.

---

## 7) Immediate execution priorities

1. Stabilize Tier-2 evaluation semantics and classify failure layers.
2. Add structured goal+plan persistence (state tables, not trace-only).
3. Add per-call policy engine and approval TTL/constraints.
4. Add durable tool-call idempotency/effect records.
5. Add deterministic goal-verification hooks before final synthesis.

These priorities keep AgentForge focused on the core objective:
**reliable goal execution, not just tool-augmented conversation.**
