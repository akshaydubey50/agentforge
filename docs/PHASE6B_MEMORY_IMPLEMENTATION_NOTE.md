# Phase 6B Memory Implementation Note

Status: implementation guide for Phase 6B only. Phase 6C context building,
Headroom, pruning workers, UI work, and new observability stacks remain out of
scope.

## Existing memory write path

Long-term memory is currently written only from
`agentsys.graph.nodes._reflect_and_save_memory()` after `synthesize_node`
successfully writes the task final answer and marks the task completed.

The current write path:

1. Load the completed task owner from `Task.owner_id`.
2. Call `MEMORY_REFLECTION_PROMPT` with request text, tools used, and the first
   500 chars of final answer.
3. Receive `MemoryReflection`.
4. If `worth_saving`, call `memory.long_term.add_memory()`.
5. Prune low-value memories for that owner.

This path is best-effort and already fails open. Phase 6B keeps that property.

## Existing reflection path

Reflection is one model call at task completion. It proposes one optional
memory with `kind`, `content`, and `importance`. The LLM currently decides both
candidate extraction and persistence eligibility.

Phase 6B changes this to "LLM proposes; code disposes": the model can propose
bounded candidates, but deterministic code validates, dedupes, merges, stores,
or ignores them.

## Existing MemoryEntry schema

`MemoryEntry` currently has:

- `id`
- `owner_id`
- optional `task_id`
- `kind`
- `content`
- `importance`
- `created_at`
- `last_accessed_at`

Phase 6B keeps this table and adds only fields required for provenance,
dedupe/merge, and safe updates:

- `meta JSONB`
- `updated_at`

No separate semantic, episodic, pinned-decision, artifact, or episode table is
created.

## Existing Chroma synchronization

`memory.long_term.add_memory()` currently embeds and writes to Chroma before
persisting the Postgres `MemoryEntry`. This can leave orphan vectors when the
database write fails.

Phase 6B makes Postgres the source of truth for memory writes and merges:

1. Validate candidate.
2. Persist or update `MemoryEntry`.
3. Index or update the matching Chroma document.

If Chroma indexing fails, the Postgres memory row remains durable and the
failure is returned to the caller for trace/test visibility. No distributed
transaction or background reconciliation service is added.

## Existing conversation persistence

Conversation state is reconstructed from existing rows:

- `Task.request_text` for the original request.
- `TaskMessage` for user follow-ups.
- `TraceSpan` rows of type `synthesize` or `quick_reply` for assistant final
  answers.

`_gather_conversation_history()` currently replays everything once a task has
any follow-up. Phase 6B keeps original rows and stores only the current rolling
summary on `Task`.

## Proposed minimal changes

- Add `memory/curation.py`.
- Add Phase 6B candidate/result schemas in existing graph schema file.
- Add `Task` rolling summary fields.
- Add `MemoryEntry.meta` and `MemoryEntry.updated_at`.
- Update `long_term.py` so durable writes happen before Chroma indexing and
  owner-scoped retrieval verifies Postgres rows.
- Replace `_reflect_and_save_memory()` internals with automatic curation.
- Update `_gather_conversation_history()` to use rolling summary plus recent
  turns when token pressure crosses a configured threshold.
- Add deterministic tests.

## Rolling-summary trigger

Use existing conversation reconstruction and `tiktoken`.

If reconstructed conversation tokens are below
`settings.conversation_summary_trigger_tokens`, preserve current behavior.

If above threshold:

- Keep the last `settings.conversation_recent_turns` turns verbatim.
- Summarize only older turns not already covered by
  `Task.rolling_summary_until`.
- Include previous `Task.rolling_summary` in the summarization prompt for
  incremental updates.
- Do not delete `TaskMessage` or `TraceSpan` rows.

Summary generation failure preserves any existing summary and falls back to a
bounded/truncated conversation block for the current turn.

## Automatic memory candidate flow

Task completion remains the main lifecycle boundary. The upgraded completion
flow collects bounded evidence from existing task rows:

- request text
- tools used
- compact subtask outcomes
- final answer preview
- existing artifact pointer metadata when present

The LLM proposes zero or more `MemoryCandidate` objects. Deterministic code
then:

1. Validates owner and task source server-side.
2. Validates supported kind and scope.
3. Rejects empty or oversized content.
4. Validates importance and confidence ranges.
5. Validates artifact references as references only.
6. Applies exact owner-scoped dedupe.
7. Applies conservative semantic merge if existing vector retrieval supports a
   high-confidence match.
8. Stores, merges, or ignores.
9. Writes Chroma after durable Postgres persistence.
10. Emits trace output with counts, ids, kinds, and reasons only.

Malformed candidates are ignored, not raised through task completion.

## Exact dedupe rules

Normalize memory content by:

- lowercasing
- trimming
- collapsing whitespace
- stripping punctuation that does not carry meaning

Hash the normalized content with SHA-256 and store it in
`MemoryEntry.meta.normalized_hash`.

Exact dedupe is scoped by:

- `owner_id`
- normalized kind
- scope
- normalized hash

Matching candidates merge into the existing row instead of creating a new row.
Owner A never dedupes against Owner B.

## Semantic merge rules

After exact dedupe, Phase 6B uses existing Chroma retrieval only when practical:

- same owner filter
- same/compatible kind
- high similarity threshold
- deterministic merge into one row

False merges are worse than duplicates, so the threshold is conservative and
the behavior is allowed to create a new row when the match is uncertain.

No cross encoder, clustering, reranker, new embedding provider, or new vector
database is introduced.

## Correction and supersession rules

Phase 6B does not attempt general truth maintenance or contradiction
detection. That would require risky inference over arbitrary natural language.

The implemented correction behavior is intentionally narrow: when a validated
candidate carries an explicit `source.supersedes_memory_id`, deterministic code
checks that the referenced memory belongs to the same owner and then marks that
older row `meta.status = "superseded"` with `meta.superseded_by` pointing to the
new memory. Invalid or cross-owner supersession hints are ignored.

## Failure behavior

Memory and summary are supportive infrastructure.

- Candidate extraction failure: no memory write; task completion continues.
- Candidate validation failure: ignored candidate.
- DB write failure: no Chroma write attempted.
- Chroma indexing failure: Postgres row remains; trace marks indexing failed.
- Summary generation failure: previous summary is preserved; the conversation
  continues with bounded original history.
- Empty/corrupt summary output: rejected; previous summary is preserved.

## Privacy and owner isolation

The owner is always loaded from the server-side `Task`, never from model output.

Every dedupe, merge, retrieval, memory write, source task lookup, and summary
update is owner scoped. Artifact references remain task scoped and must only
refer to the same owner's task.

If owner or source ownership cannot be established, the operation fails closed
by ignoring the candidate or skipping the summary update.

## Model-call count impact

The existing task-completion reflection call becomes a bounded candidate
extraction call. There is no additional model call per message.

Rolling summary adds a model call only when token pressure crosses the
configured threshold. Incremental summarization prevents repeatedly summarizing
the full conversation.

Dedupe, validation, and merge decisions are deterministic. Semantic merge
reuses existing embeddings/vector search; it adds no reranker or judge call.

## Explicitly excluded Phase 6C/6D work

Not implemented in Phase 6B:

- context builder
- step-loop memory retrieval
- token budget allocator
- memory prompt injection policy
- reranker or cross encoder
- Headroom or compression adapter
- lifecycle decay/archive worker
- reconciliation service
- memory UI
- project/global memory
- observability framework changes
- Gmail send or additional external-write tools
