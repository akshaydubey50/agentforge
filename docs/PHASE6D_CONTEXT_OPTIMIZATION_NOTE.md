# Phase 6D Context Optimization Note

Status: design note first. This phase may implement compression seams,
deterministic token/cost benchmarks, conservative duplicate removal, model
window metadata, memory lifecycle archival, and focused quality assertions. It
must not add a new memory database, context builder, RAG system, artifact store,
external observability stack, eval framework, or Headroom dependency unless
measurements prove a concrete gap.

North star:

```text
Store broadly.
Retrieve selectively.
Send minimally.
Preserve recoverability.
Measure quality before claiming savings.
```

Phase 6D optimizes for minimum sufficient context, not the smallest possible
prompt.

## 1. Current token flow

All first-party agent model calls still pass through `agentsys.llm.complete`
or `agentsys.llm.structured_complete`, and `agentsys.cost.record_llm_call`
records provider-reported prompt, completion, cached, and cost fields after
successful calls.

Main token sources by prompt path:

| Source | Current carrier | Notes |
| --- | --- | --- |
| static/system prompt | `graph/prompts.py` prompt templates | Cache-friendly static prefix exists for agent-step and tool-selection. |
| tool definitions | `ToolRegistry.describe()` text plus JSON Schema | Protected, large, repeated on sketch, step, selection, and sub-agent calls. |
| current user goal | `Task.request_text` and current `TaskMessage` turn | Protected. API request text already has a char cap. |
| current subtask | `Subtask.description`, success criteria, retry feedback | Protected when selecting/executing a tool. |
| rolling summary | `Task.rolling_summary` rendered by `_gather_conversation_history` | Already lossy and bounded by Phase 6B settings. |
| recent conversation | reconstructed `TaskMessage` and final-answer spans | Recent turns are preserved verbatim by Phase 6B. |
| durable memories | `context.build_agent_step_context` selected memory block | Step-loop memory is budgeted, owner-scoped, and Postgres-verified. |
| prior-task evidence | `_gather_prior_context` from `Subtask.output` rows | Recent full, older truncated. Includes failed/escalated/skipped steps. |
| dead-call history | `deadcalls.describe(task_id)` | Compact guardrail text, protected. |
| RAG/tool results | carried through subtask outputs and prior context | RAG itself remains a tool boundary; the graph sees answer/source payloads. |
| artifacts | `artifacts.spill()` pointer dict, summary, preview, path | Full text stays in workspace; prompt gets digest/preview/reference. |
| planner state | latest `plan` trace span or sketch span | Protected because it drives the next action. |

## 2. Largest context contributors

Known significant contributors:

1. Tool catalogue and schemas: repeated protected prefix cost. The priority is
   deterministic ordering for prompt cache, not truncation.
2. Prior step outputs: already fixed partially by recency compaction and
   artifact spill, but large tool/RAG outputs still dominate until spilled or
   compacted.
3. Conversation history: Phase 6B rolling summary controls older turns but
   recent turns remain verbatim by design.
4. Durable memory: bounded to `agent_step_memory_budget_tokens`, but duplicated
   memories can still waste the budget.
5. Synthesis and verification prompts: consume completed subtask outputs and
   conversation, but Phase 6D focuses on the `agent_step` context seam first.

Do not optimize a source until measurements show it is material for a fixture.

## 3. Existing compression/truncation mechanisms

- `max_request_text_length` caps submitted task/follow-up text.
- `_gather_prior_context` keeps recent step outputs in full and truncates older
  outputs.
- `artifacts.spill` stores oversized tool outputs under `_artifacts` and sends
  digest, preview, total size, and a retrievable path.
- `artifacts.for_synthesis` removes internal artifact mechanics before final
  user-facing synthesis.
- `_gather_conversation_history` uses Phase 6B rolling summary plus recent
  turns under token pressure.
- `context.build_agent_step_context` budgets durable memory after protected
  prompt content.
- `events.truncate` limits live event payload shape.
- `LlmCall` is provider ground truth for post-call usage/cost.

## 4. What must never be lossy-compressed

Protected/lossless:

- system, safety, and operating instructions;
- current user goal and current follow-up turn;
- current subtask description and success criteria;
- tool schemas needed for the current decision;
- approval/policy state, effect identity, and structured arguments;
- critical verification criteria;
- dead-call loop-prevention state;
- planner state required for the next action;
- pinned decisions required for correctness.

If these exceed the usable model input budget, the correct behavior is a
controlled capacity failure, not silent truncation.

## 5. What can be compressed

Compressible:

- large tool outputs;
- verbose RAG answers/source lists returned as tool results;
- old prior-step evidence already represented by step status and summary;
- logs and repeated search snippets;
- large artifact excerpts;
- redundant conversation material already represented in rolling summary.

Optional/droppable:

- lower-ranked durable memories;
- redundant tool/RAG output with the same artifact/source/tool reference;
- low-value background information;
- artifact previews when a compact reference and summary are enough.

## 6. Headroom evaluation

Current public Headroom documentation describes a context optimization layer
that compresses tool outputs, DB results, file reads, RAG results, API
responses, logs, search results, and some conversation/history content before
the model call. It provides Python, TypeScript, proxy, framework, and MCP
integration surfaces, and its CCR mechanism stores originals locally and gives
the model a retrieval tool for full originals. Current context-management docs
say the pipeline compresses only the live zone, preserving system prompts,
tool definitions, and older turns for prompt-cache stability.

Headroom would potentially help AgentForge where bulky latest tool outputs,
large JSON/search/log payloads, or long tool-heavy sessions remain material
after AgentForge's own spill, prior-context compaction, and memory selection.

Headroom is not integrated in this phase because:

- AgentForge already has rolling summaries, selective memory, context budgets,
  artifact spill, and progressive disclosure.
- AgentForge needs deterministic, block-aware compression policy tied to
  protected/compressible/optional context classes before adding a dependency.
- Headroom's native Windows path currently falls back to building a Rust
  extension and requires MSVC/Rust tooling; Docker/proxy remains possible but
  adds an operational service.
- Headroom's CCR tool injection would need a clean tool boundary so it does
  not bypass AgentForge policy, tracing, or artifact ownership checks.
- The current requirement is to prove representative savings and quality
  preservation first.

The Phase 6D implementation should therefore add a small local
`ContextCompressor` seam and built-in bounded/reference compression. A future
Headroom adapter is justified only if benchmark fixtures show a persistent
large compressible payload class that the built-in path cannot reduce while
preserving key evidence.

## 7. Compression seam

Use one narrow seam inside `src/agentsys/context.py`:

```text
ContextCompressor
  compress(block, budget)
  retrieve_original(ref)
```

The built-in implementation should be deterministic and dependency-free:

- protected blocks pass through unchanged;
- small compressible blocks pass through unchanged;
- medium compressible blocks keep bounded useful excerpts;
- large compressible blocks become preview + tail/error snippets +
  artifact/reference metadata when available;
- failures fall back to bounded uncompressed excerpts, never "send everything".

Do not make Headroom, or any compressor, the context builder.

## 8. Progressive disclosure strategy

Reuse existing artifact spill:

```text
small result  -> inline fully
medium result -> bounded useful excerpt
large result  -> compact preview + artifact/reference
later need    -> model asks for specific section via existing file/artifact path
```

Do not automatically reinsert full artifacts into later prompts. A 20k-token
execution log should yield failure/error lines, tail, size, and a reference,
not the full log.

## 9. Duplicate-context strategy

Phase 6D should implement only low-risk deterministic duplicate removal:

- same normalized content;
- same memory id;
- same source/artifact id;
- same retrieval document/chunk id;
- same tool result reference.

False deletion is worse than some redundancy. Do not add semantic clustering
or cross-encoder dedupe in this phase.

## 10. Model-context-window handling

Centralize model metadata in `context.py` or a small adjacent helper:

```text
model -> context_window_tokens, default_output_reserve_tokens
```

Known models get explicit metadata. Unknown models use a conservative fallback.
Provider details must not be scattered through graph nodes.

The usable input budget is:

```text
context_window_tokens - reserved_output_tokens
```

The selected context must not knowingly exceed provider limits.

## 11. Prompt-cache strategy

Stable prefix:

- static agent instructions;
- tool descriptions/schemas;
- stable operating rules.

Dynamic zone:

- request/follow-up;
- rolling summary and recent turns;
- durable memory;
- prior evidence, RAG/tool results, artifacts, dead calls.

Implementation rules:

- preserve stable prompt ordering;
- keep tool descriptions deterministically ordered;
- do not insert timestamps, run ids, or metrics above the static/tool prefix;
- do not implement provider-specific cache APIs in Phase 6D unless the LLM
  layer already supports them cleanly.

## 12. Memory lifecycle design

No new tables. Reuse `MemoryEntry.meta.status`:

- `active`: default retrievable status;
- `superseded`: already used by Phase 6B explicit supersession;
- `archived`: retained in Postgres and Chroma, excluded from default retrieval.

Archive rather than delete in Phase 6D.

Automatic archive criteria should be conservative:

```text
importance <= threshold
AND not pinned_decision
AND old enough
AND usage_count/retrieval_count is zero or stale
AND status is active/blank
```

Pinned decisions are never archived merely due to age. Reinforced recent
semantic memory is retained. Episodic memories are the safest initial archive
candidate when old and unused.

## 13. Token/cost measurements

Add representative deterministic fixtures:

- short conversation;
- long conversation with rolling-summary equivalent;
- memory-heavy task;
- large tool-result task;
- RAG-heavy/tool-result task.

Measure for baseline and optimized paths:

- raw/available context tokens;
- selected pre-compression tokens;
- selected post-compression tokens;
- model input estimate;
- reserved output tokens;
- cached tokens if present in provider data, otherwise null/unknown;
- memory tokens;
- conversation tokens;
- summary tokens;
- tool/RAG tokens;
- tokens avoided;
- cost estimate from local pricing when possible;
- deterministic quality result.

Synthetic benchmark fixtures must be labeled representative and must not be
presented as production savings.

## 14. Quality evaluation methodology

Use deterministic assertions in Phase 6D:

- protected evidence remains verbatim;
- required memory survives selection;
- key evidence strings remain available either inline or by reference;
- duplicate removal does not collapse distinct evidence;
- optimized selected tokens are less than or equal to baseline;
- route/action invariants are unchanged for deterministic fixture cases.

Do not add DeepEval, RAGAS, or a new judge framework. Record model-judge
integration as later work.

## 15. Rollback/fallback behavior

- Compression failure: bounded uncompressed excerpt/reference, not full body.
- Headroom unavailable: built-in/no-op seam continues safely.
- Artifact retrieval failure: preserve reference and report unavailable
  evidence.
- Tokenizer failure: conservative estimate and bounded behavior.
- Lifecycle update failure: no deletion, no memory corruption, retrieval still
  works with existing active rows.
- Mandatory overflow: reduce optional context to zero, compress eligible
  adjacent verbose blocks, then return an explicit capacity error if protected
  content still exceeds the usable input budget.

## 16. Exact implementation scope

In scope:

- extend `agentsys.context` with block kinds, compression classes, duplicate
  removal, model-window metadata, richer metrics, benchmark fixtures, and a
  narrow compressor seam;
- update `agent_step_node` to fail explicitly on known mandatory overflow;
- extend memory retrieval to reinforce only selected memories;
- add archival helpers in `memory.long_term`;
- exclude archived memory by default and allow explicit archived retrieval;
- add deterministic Phase 6D tests and add pure benchmark tests to Tier 1;
- document Headroom as deferred/rejected for now.

## 17. Explicitly excluded work

Out of scope:

- Phase 7 observability framework integrations;
- Langfuse, DeepEval changes, NeMo Guardrails, Guardrails AI, RAGAS,
  Grafana, Prometheus;
- frontend Context Inspector or Waku/React Flow UI;
- Gmail send or new external actions;
- GraphRAG, a new vector database, new memory database, new RAG system;
- a new artifact store;
- automatic hard deletion of user memory;
- complicated contradiction reasoning;
- a background worker unless a later phase proves it is needed.

## 18. Ponytail reductions

Rejected additions:

- no Headroom dependency in Phase 6D implementation;
- no second context builder;
- no generic compression DSL;
- no new memory lifecycle table;
- no new token-accounting source;
- no provider-specific cache API;
- no hard-delete lifecycle policy;
- no semantic duplicate clustering.

Existing mechanisms are sufficient for the first production-safe cut:
Postgres truth, Chroma candidate retrieval, rolling summary, artifact spill,
context budgets, `LlmCall` token accounting, and trace metadata.
