# Phase 6C Context Engineering Note

Status: design note first. Do not treat this as permission to add a new
framework, table, service, reranker, or compression dependency. The
implementation should be the smallest production-safe context-selection layer
that reuses the current graph, memory, tracing, token accounting, and artifact
spill mechanisms.

North star:

```text
STORE BROADLY
RETRIEVE SELECTIVELY
SEND MINIMALLY
PRESERVE RECOVERABILITY
```

Phase 6B made storage broader and safer. Phase 6C decides what is sent to a
model call.

## 1. Current Context Construction

All first-party agent model calls pass through `src/agentsys/llm.py`:

- `complete(prompt)` sends one user message and returns text plus the raw
  completion for `cost.record_llm_call`.
- `structured_complete(prompt, response_model)` sends one user message with a
  structured response format.
- `embed_texts()` is used for durable memory embeddings.

The current graph keeps LangGraph state small: `AgentState` carries `task_id`
and transient routing. Almost every model prompt is built in
`src/agentsys/graph/nodes.py` from durable Postgres rows and Redis scratch
state.

Important prompt paths:

- `triage_node`
  - Context source: current turn from `_turn_text`, conversation block from
    `_gather_conversation_history`.
  - Prompt: `TRIAGE_PROMPT`.
  - Purpose: choose `quick` vs `full`.
- `quick_reply_node`
  - Context source: current turn and conversation only.
  - Prompt: `QUICK_REPLY_PROMPT`.
  - No tools, subtasks, or memory writes.
- `sketch_node`
  - Context source: original request, tool catalogue from `ToolRegistry.describe`,
    and fixed `k=3` long-term memory retrieval.
  - Prompt: `SKETCH_PROMPT`.
  - Stores sketch only as a `TraceSpan`.
- `agent_step_node`
  - Context source: original request, rolling-summary conversation block,
    current plan from latest `plan` span or sketch span, full tool catalogue,
    `_gather_prior_context`, dead-call prompt block, and step budget values.
  - Prompt: `AGENT_STEP_PROMPT`.
  - This is the main Phase 6C target.
- `_execute_subtask`
  - Fresh first attempt reuses the tool choice selected by `agent_step_node`.
  - Retry or missing preselection builds `TOOL_SELECTION_PROMPT` from subtask
    description, tool catalogue, prior context, and revision feedback.
  - Reasoning-only uses `REASONING_ONLY_PROMPT` with prior context and revision
    feedback.
- `_review_subtask`
  - Deterministic verification runs first.
  - LLM review sees only subtask description, tool used, tool success, and
    subtask output.
- `_verify_goal_before_synthesis`
  - Deterministic goal verification runs first.
  - LLM fallback sees original request, conversation block, and recorded subtask
    outputs.
- `synthesize_node`
  - Context source: original request, conversation block, and completed subtask
    outputs after `artifacts.for_synthesis()` removes spill mechanics.
- `_reflect_and_save_memory`
  - Context source: request text, tools used, bounded subtask evidence, and
    final answer preview.
  - It writes memory but does not influence the current prompt.
- `DelegateSubagentTool.run`
  - Separate bounded loop with `SUBAGENT_STEP_PROMPT`.
  - Context source: delegated goal, filtered tool catalogue, compact step
    history, and remaining-step count.
- `artifacts._digest`
  - Summarizes oversized tool outputs for spill pointers. It is best-effort and
    fails open to preview-only spill metadata.

RAG is separate:

- `knowledge_search` calls `rag-api /v1/ask`.
- `src/rag` performs hybrid retrieval, optional LLM reranking, grounded
  generation, citation verification, and confidence scoring internally.
- The graph only sees the RAG answer/source-title/confidence payload as a tool
  result.

## 2. Existing Reusable Mechanisms

Reuse these:

- `agentsys.llm` for all agent LLM calls and retry behavior.
- `cost.record_llm_call` for provider-reported token accounting.
- `graph.tracing.span` for durable trace rows and live event output.
- `nodes._estimate_tokens`, currently backed by `tiktoken`.
- `nodes._gather_conversation_history`, which already returns rolling summary
  plus recent turns under Phase 6B pressure.
- `nodes._gather_prior_context`, which already applies recency compaction to
  step evidence.
- `long_term.retrieve_relevant`, which already owner-filters in Chroma and
  verifies rows in Postgres.
- `ToolRegistry.describe`, which already provides model-facing tool prose plus
  generated JSON Schema.
- `artifacts.spill` and `artifacts.for_synthesis`, which implement progressive
  disclosure for oversized tool results.
- Existing settings rather than a new config subsystem.

Do not add a second tracing stack, another vector store, a new prompt library,
or a new planner table for Phase 6C.

## 3. Problems Found

- `agent_step_node` still assembles context by string formatting directly in
  the node.
- There is no shared context block model, so the code cannot report selected
  and dropped context consistently.
- Long-term memory retrieval is fixed at `k=3` and only happens in
  `sketch_node`.
- `agent_step_node` does not see pinned decisions or preferences unless the
  initial sketch happened to retrieve them and the model preserved them in the
  plan.
- Prior step context has local recency compaction but no aggregate prompt
  budget.
- Conversation has Phase 6B rolling summary, but no consumer-level priority
  model.
- Tool catalogue size is protected by cache-friendly ordering but not measured
  or budgeted.
- RAG evidence arrives as a tool result, not as first-class source chunks in the
  agent context. That is fine, but it must be budgeted like other tool evidence.
- Trace spans record node inputs and outputs, but not what context was selected
  or omitted.

## 4. Context Sources Available

Mandatory:

- Static prompt instructions.
- Tool catalogue when the call can select tools.
- Current user request or current follow-up turn.
- Runtime limits such as remaining step count.

Conversation:

- `Task.request_text`.
- `TaskMessage` follow-ups.
- Assistant final answers from `TraceSpan` rows of type `synthesize` and
  `quick_reply`.
- `Task.rolling_summary` plus recent turns from Phase 6B.

Durable memory:

- `MemoryEntry.kind`: `pinned_decision`, `preference`, `semantic`, `episodic`,
  `artifact_reference`, and legacy `fact` as semantic.
- `MemoryEntry.meta.scope`, confidence, status, source task, artifact refs, and
  Chroma indexing state.

Execution:

- Current plan from latest `plan` span or sketch.
- Subtasks with statuses and outputs.
- Tool calls and verification failures.
- Dead-call ledger in Redis.
- Revision feedback from review rows.
- Escalation context for approval resumes.

Knowledge:

- RAG answer/source/confidence returned by `knowledge_search` as tool output.
- Separate RAG chunks stay inside `src/rag` for now.

Artifacts:

- Spill pointers and summaries in subtask output.
- Deliverables and spillover files under the task workspace.
- Artifact dereference remains through `file_io`.

## 5. Context Selection Rules

Initial Phase 6C should focus on `agent_step_node`.

Rules:

- Never drop static instructions or the current request.
- Keep the tool catalogue before dynamic task context to preserve provider
  cache behavior.
- Keep step budget and current plan; compact them rather than dropping them.
- Prefer recent verbatim conversation over older verbatim conversation.
- Prefer rolling summary over old raw turns.
- Include directly relevant pinned decisions first.
- Include relevant preferences before generic semantic memories.
- Include episodic memories only when they match the task or failure pattern.
- Include artifact-reference memories as pointers, not artifact bodies.
- Drop low-relevance memory before dropping recent conversation or current task
  evidence.
- Treat RAG output as tool evidence already present in prior context; do not
  separately query RAG during context assembly.

## 6. Token-Budget Strategy

Use approximate token counting with the existing `tiktoken` path. Provider
reported `LlmCall.prompt_tokens` remains ground truth after the call.

Add small settings, not a new budgeting service:

- default context budget for `agent_step`.
- reserved output tokens for `agent_step`.
- memory context budget.
- maximum selected memory count.

Budget handling:

1. Measure protected static content and current request.
2. Build dynamic context blocks with priority and token estimates.
3. Include protected blocks.
4. Include optional blocks in priority order while budget remains.
5. For memory blocks, drop whole memories rather than truncating them into
   ambiguous fragments.
6. For prior context and conversation, reuse existing compaction functions.
7. If protected content alone exceeds budget, continue but trace an
   over-budget condition; do not silently delete required instructions.

This is not pure token minimization. It is minimum sufficient context under an
explicit priority model.

## 7. Priority Model

Suggested priority order for `agent_step`:

1. Static `AGENT_STEP_PROMPT` instructions and tool catalogue.
2. Original request and current conversation block from Phase 6B.
3. Current plan and step budget.
4. Dead-call warning block.
5. Prior task evidence from `_gather_prior_context`.
6. Pinned decisions relevant to request/current plan/prior failures.
7. Preferences relevant to the task.
8. Semantic memories.
9. Episodic memories.
10. Artifact-reference memories.

For the first implementation, priority can be encoded as integers on context
blocks. Do not add a learned reranker.

## 8. Memory Retrieval Strategy

Step-loop memory retrieval should be selective:

- Query text should combine current request, current plan, and prior context
  summary headings, not every tool output byte.
- Retrieve per owner only.
- Retrieve active memory only. Superseded rows should not be prompt candidates.
- Always include highly relevant `pinned_decision` rows first when retrieval
  returns them.
- Retrieve compatible kinds separately or filter retrieved rows by kind after
  Postgres verification.
- Keep exact memory text short and include kind labels.
- Trace retrieved count, selected count, dropped count, and selected ids.

Do not write reinforcement, decay, archive, or contradiction handling in 6C.

## 9. RAG Interaction

Do not merge `src/rag` into the agent context builder.

The current boundary is acceptable:

- Agent chooses `knowledge_search`.
- RAG retrieves chunks and generates a cited answer.
- Agent receives that answer as tool output.
- Future prompts see it through `_gather_prior_context`.

Phase 6C can budget RAG-derived tool outputs like other prior context. It
should not independently retrieve RAG chunks or call the RAG reranker.

## 10. Rolling-Summary Interaction

The context builder should consume `_gather_conversation_history()` as the
conversation source for now. That function already owns:

- reconstructing turns from `Task`, `TaskMessage`, and `TraceSpan`;
- token-triggered rolling summary;
- recent-turn preservation;
- fail-open summary behavior.

Do not duplicate rolling-summary logic in the context builder. Later, it can
return structured blocks instead of a string, but that is not required for the
first production cut.

## 11. Recent-Message Preservation

Recent user and assistant turns should remain verbatim through Phase 6B's
`conversation_recent_turns` setting. If the aggregate context exceeds budget,
drop optional memory before altering the conversation block.

The current follow-up turn remains mandatory because `_turn_text()` is the
actual user request for triage and quick reply, and `agent_step_node` still
works from the original task request plus conversation.

## 12. Tool-Result Handling

The existing `_gather_prior_context()` should remain the main tool-result
source:

- recent steps stay verbatim;
- older outputs truncate;
- failed/escalated/skipped outputs are included because they are planning
  evidence;
- spill pointers remain available to agent steps;
- synthesis strips spill mechanics through `artifacts.for_synthesis()`.

Do not copy raw `ToolCall.output` into the context builder separately.

## 13. Artifact Handling

Artifacts remain progressive-disclosure references:

- Store full oversized outputs on disk.
- Send digest/preview/path in step context.
- Let the model dereference with `file_io` only when needed.
- Never insert full artifact bodies into durable memory.
- Artifact-reference memories may be included as compact metadata only.

## 14. Failure And Fallback Behavior

- Context selection must fail open to existing behavior where possible.
- Memory retrieval failures should omit memory and trace the failure.
- Token counting failures should fall back to rough character estimates.
- If the selected context is over budget, trace it; do not mutate task state.
- Do not block a task because memory or optional context is unavailable.
- Provider context-window errors remain permanent LLM errors in `llm.py`.

## 15. Owner Isolation

Context builder must never trust owner ids from model output.

- Load `owner_id` from the server-side `Task`.
- Long-term memory retrieval must pass that owner id.
- Returned Chroma ids must already be verified through Postgres by
  `long_term.retrieve_relevant`.
- Artifact references must still be dereferenced only through existing
  task-owned artifact/file APIs.
- Eval tasks should not write memory; 6C retrieval can read only the eval
  owner's memory if any exists.

## 16. Observability

Emit one trace-friendly context selection summary per `agent_step` call:

- selected block names;
- selected token estimate;
- dropped block names/counts;
- retrieved memory count;
- selected memory ids/kinds;
- over-budget flag;
- retrieval error class if any.

Keep trace output metadata-only. Do not duplicate full prompts or sensitive
tool arguments just for observability.

## 17. Tests

Focused deterministic tests:

- token estimator falls back safely;
- context builder includes protected blocks;
- memory selection respects owner id via mocked `long_term.retrieve_relevant`;
- pinned decisions sort before generic semantic memories;
- low-priority memories are dropped under a memory budget;
- `agent_step_node` prompt includes selected memory context;
- memory retrieval failure does not fail `agent_step_node`;
- existing Phase 1-6B regression suites stay green.

Avoid model calls in these tests by monkeypatching `structured_complete`.

## 18. Explicitly Excluded Work

Not in Phase 6C:

- Headroom or any compression dependency.
- New memory tables.
- Project/global memory scopes.
- Learned reranking or cross encoders.
- RAG chunk injection into graph prompts.
- New planner table.
- Prompt cache provider-specific controls.
- Memory lifecycle reinforcement/decay/archive.
- UI changes.
- Background reconciliation workers.

## 19. Smallest Implementation Plan

1. Add `src/agentsys/context.py` with context blocks, token estimation,
   memory retrieval/selection, and an `build_agent_step_context` function.
2. Add minimal settings for `agent_step` budget and memory selection.
3. Update only `agent_step_node` to use the context builder for dynamic context
   values while preserving `AGENT_STEP_PROMPT`.
4. Trace context-selection metrics in the existing `agent_step` span.
5. Add focused tests for context selection and the `agent_step_node` seam.

Do not refactor every prompt call in this phase. Once `agent_step_node` is
stable, the same block model can be reused by tool-selection, synthesis, and
sub-agent loops in later phases.
