import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agentsys import context
from agentsys.db.models import MemoryEntry, Task, User
from agentsys.db.session import get_session, init_db
from agentsys.graph import nodes
from agentsys.graph.schemas import SketchOutput
from agentsys.memory import curation, long_term
from agentsys.memory.long_term import RetrievedMemory
from agentsys.policy import PolicyDecisionType, decide
from agentsys.tools.file_io import FileIOTool


def setup_module() -> None:
    init_db()


def _owner(prefix: str = "phase6d") -> str:
    marker = uuid.uuid4().hex
    with get_session() as session:
        user = User(
            google_sub=f"{prefix}-{marker}",
            email=f"{prefix}-{marker}@example.com",
            name=prefix,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


def _task(owner_id: str) -> str:
    with get_session() as session:
        task = Task(request_text="Phase 6D task", owner_id=owner_id)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _row(
    owner_id: str,
    *,
    kind: str = "semantic",
    content: str = "memory content",
    importance: int = 1,
    status: str = "active",
    created_at: datetime | None = None,
    meta: dict | None = None,
) -> str:
    with get_session() as session:
        entry = MemoryEntry(
            owner_id=owner_id,
            kind=kind,
            content=content,
            importance=importance,
            created_at=created_at or datetime.now(timezone.utc),
            meta={"status": status, **(meta or {})},
        )
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry.id


def _memory(mid: str, kind: str, content: str, *, score: float = 0.8, importance: int = 3):
    return RetrievedMemory(
        id=mid,
        content=content,
        kind=kind,
        importance=importance,
        similarity=score,
        weighted_score=score,
    )


class _FakeCollection:
    def __init__(self, ids: list[str], distances: list[float] | None = None):
        self.ids = ids
        self.distances = distances or [0.1 for _ in ids]

    def count(self):
        return len(self.ids)

    def query(self, **kwargs):
        return {
            "ids": [self.ids],
            "documents": [[f"doc {mid}" for mid in self.ids]],
            "metadatas": [[{} for _ in self.ids]],
            "distances": [self.distances],
        }


def _build(monkeypatch, **overrides):
    monkeypatch.setattr(context.settings, "agent_step_context_budget_tokens", overrides.pop("budget", 10_000))
    monkeypatch.setattr(context.settings, "agent_step_reserved_output_tokens", overrides.pop("reserve", 0))
    monkeypatch.setattr(context.settings, "agent_step_memory_budget_tokens", overrides.pop("memory_budget", 500))
    monkeypatch.setattr(context.settings, "agent_step_memory_max_items", overrides.pop("max_items", 6))
    values = {
        "task_id": "task",
        "owner_id": "owner",
        "request_text": "current request",
        "conversation": "",
        "plan": "plan",
        "prior_context": "",
        "dead_calls": "",
        "tool_descriptions": "tools",
        "steps_taken": 0,
        "steps_remaining": 12,
    }
    values.update(overrides)
    return context.build_agent_step_context(**values)


def test_protected_context_never_compressed_or_dropped(monkeypatch):
    monkeypatch.setattr(context.long_term, "retrieve_relevant", lambda *args, **kwargs: [])
    built = _build(monkeypatch, request_text="DO NOT COMPRESS THIS GOAL")

    assert built.capacity_error is None
    assert built.metrics["protected_tokens"] > 0
    assert built.metrics["compressed_block_count"] == 0
    assert built.plan == "plan"


def test_optional_memory_drops_before_protected_blocks(monkeypatch):
    monkeypatch.setattr(
        context.long_term,
        "retrieve_relevant",
        lambda *args, **kwargs: [_memory("m1", kwargs["kind"], "large memory " * 100)]
        if kwargs["kind"] == "semantic"
        else [],
    )
    monkeypatch.setattr(context, "estimate_tokens", lambda text, model=None: 10 if text.startswith("- [") else 1)

    built = _build(monkeypatch, memory_budget=5)

    assert built.metrics["selected_memory_count"] == 0
    assert built.metrics["dropped_memory_count"] == 1
    assert built.plan == "plan"


def test_large_tool_output_becomes_bounded_reference_form():
    block = context.ContextBlock(
        "tool_log",
        ("INFO passed\n" * 200) + "ERROR deployment failed\n" + ("INFO cleanup\n" * 200),
        1,
        required=True,
        compression=context.COMPRESSION_COMPRESSIBLE,
        metadata={"artifact_path": "_artifacts/log.json"},
    )

    compressed, results = context.compress_context_blocks([block], budget_tokens=80)

    assert results[0].compressed is True
    assert "ERROR deployment failed" in compressed[0].text
    assert "_artifacts/log.json" in compressed[0].text
    assert compressed[0].tokens <= block.tokens


def test_artifact_progressive_disclosure_reference_is_preserved():
    block = context.ContextBlock(
        "artifact_preview",
        "preview\nfull_output_path: _artifacts/abc_tool.json\n" + ("body " * 500),
        1,
        required=True,
        compression=context.COMPRESSION_COMPRESSIBLE,
    )

    compressed, _ = context.compress_context_blocks([block], budget_tokens=80)

    assert "_artifacts/abc_tool.json" in compressed[0].text
    assert "Reference for full original" in compressed[0].text


def test_exact_duplicate_context_removed():
    blocks = [
        context.ContextBlock("a", "same evidence", 1, metadata={"source_id": "s1"}),
        context.ContextBlock("b", "same evidence", 2, metadata={"source_id": "s1"}),
    ]

    deduped, removed = context.deduplicate_context_blocks(blocks)

    assert [b.name for b in deduped] == ["a"]
    assert removed == 1


def test_duplicate_removal_keeps_distinct_evidence():
    blocks = [
        context.ContextBlock("a", "revenue was 10", 1, metadata={"source_id": "s1"}),
        context.ContextBlock("b", "revenue was 11", 2, metadata={"source_id": "s2"}),
    ]

    deduped, removed = context.deduplicate_context_blocks(blocks)

    assert len(deduped) == 2
    assert removed == 0


def test_model_context_window_mapping_known_model():
    assert context.model_context_window("openai/gpt-4o-mini") == 128_000
    assert context.default_output_reserve("openai/gpt-4o-mini") == 2_000


def test_unknown_model_uses_safe_fallback():
    assert context.model_context_window("unknown/provider-model") == context.UNKNOWN_CONTEXT_WINDOW_TOKENS
    assert context.default_output_reserve("unknown/provider-model") == context.UNKNOWN_OUTPUT_RESERVE_TOKENS


def test_output_reserve_is_respected(monkeypatch):
    monkeypatch.setattr(context.long_term, "retrieve_relevant", lambda *args, **kwargs: [])

    built = _build(monkeypatch, budget=100, reserve=40)

    assert built.metrics["usable_input_budget_tokens"] == 60


def test_mandatory_overflow_is_explicit(monkeypatch):
    monkeypatch.setattr(context, "estimate_tokens", lambda text, model=None: 100)

    built = _build(monkeypatch, budget=50, reserve=0)

    assert built.capacity_error == "protected_context_exceeds_model_input_budget"
    assert built.metrics["over_budget"] is True


def test_compression_failure_uses_bounded_fallback():
    class FailingCompressor(context.DeterministicContextCompressor):
        def compress(self, block, *, budget_tokens):
            raise RuntimeError("headroom unavailable")

    block = context.ContextBlock(
        "tool_log",
        "secret " * 1000,
        1,
        required=True,
        compression=context.COMPRESSION_COMPRESSIBLE,
        metadata={"artifact_path": "_artifacts/failure.json"},
    )

    compressed, results = context.compress_context_blocks(
        [block],
        budget_tokens=50,
        compressor=FailingCompressor(),
    )

    assert results[0].failed is True
    assert compressed[0].tokens < block.tokens
    assert "_artifacts/failure.json" in compressed[0].text


def test_headroom_unavailable_fallback_path_is_dependency_free():
    report = context.run_representative_context_benchmark()

    assert report
    assert all(row["optimized_tokens"] <= row["baseline_tokens"] for row in report)


def test_archived_memory_excluded_from_default_retrieval(monkeypatch):
    owner_id = _owner("archived-default")
    archived_id = _row(owner_id, status="archived", content="archived memory")
    monkeypatch.setattr(long_term, "embed_texts", lambda texts: [[0.0]])
    monkeypatch.setattr(long_term, "get_memory_collection", lambda: _FakeCollection([archived_id]))

    default = long_term.retrieve_relevant("query", owner_id=owner_id, k=3)
    explicit = long_term.retrieve_relevant("query", owner_id=owner_id, k=3, include_archived=True)

    assert default == []
    assert [m.id for m in explicit] == [archived_id]


def test_pinned_memory_is_not_auto_archived():
    owner_id = _owner("pin-archive")
    old = datetime.now(timezone.utc) - timedelta(days=365)
    memory_id = _row(
        owner_id,
        kind="pinned_decision",
        importance=1,
        created_at=old,
        content="Pinned decision",
    )

    archived = long_term.archive_low_value_memories(owner_id, now=datetime.now(timezone.utc))

    with get_session() as session:
        row = session.get(MemoryEntry, memory_id)
    assert archived == 0
    assert row.meta["status"] == "active"


def test_low_value_stale_memory_can_archive():
    owner_id = _owner("low-archive")
    old = datetime.now(timezone.utc) - timedelta(days=365)
    memory_id = _row(owner_id, kind="episodic", importance=1, created_at=old)

    archived = long_term.archive_low_value_memories(owner_id, now=datetime.now(timezone.utc))

    with get_session() as session:
        row = session.get(MemoryEntry, memory_id)
    assert archived == 1
    assert row.meta["status"] == "archived"
    assert row.meta["archive_reason"] == "low_value_stale_unused"


def test_selected_memory_usage_gets_reinforced(monkeypatch):
    owner_id = _owner("selected-use")
    memory_id = _row(owner_id, kind="semantic", content="Use Postgres as truth", importance=5)
    monkeypatch.setattr(
        context.long_term,
        "retrieve_relevant",
        lambda *args, **kwargs: [_memory(memory_id, "semantic", "Use Postgres as truth", importance=5)]
        if kwargs["kind"] == "semantic"
        else [],
    )

    built = _build(monkeypatch, owner_id=owner_id)

    with get_session() as session:
        row = session.get(MemoryEntry, memory_id)
    assert built.metrics["selected_memory_ids"] == [memory_id]
    assert row.meta["usage_count"] == 1
    assert row.meta["last_used_at"]


def test_raw_retrieval_candidate_alone_does_not_count_as_usage(monkeypatch):
    owner_id = _owner("raw-candidate")
    memory_id = _row(owner_id, kind="semantic", content="Candidate only")
    monkeypatch.setattr(long_term, "embed_texts", lambda texts: [[0.0]])
    monkeypatch.setattr(long_term, "get_memory_collection", lambda: _FakeCollection([memory_id]))

    assert long_term.retrieve_relevant("query", owner_id=owner_id, k=3)

    with get_session() as session:
        row = session.get(MemoryEntry, memory_id)
    assert "usage_count" not in row.meta
    assert "last_used_at" not in row.meta


def test_postgres_remains_truth_for_missing_chroma_hits(monkeypatch):
    owner_id = _owner("pg-truth")
    existing_id = _row(owner_id, kind="semantic", content="Existing memory")
    missing_id = f"missing-{uuid.uuid4().hex}"
    monkeypatch.setattr(long_term, "embed_texts", lambda texts: [[0.0]])
    monkeypatch.setattr(long_term, "get_memory_collection", lambda: _FakeCollection([missing_id, existing_id]))

    results = long_term.retrieve_relevant("query", owner_id=owner_id, k=3)

    assert [m.id for m in results] == [existing_id]


def test_context_token_metrics_are_emitted(monkeypatch):
    monkeypatch.setattr(context.long_term, "retrieve_relevant", lambda *args, **kwargs: [])

    built = _build(monkeypatch, prior_context="tool result", conversation="conversation")

    for key in (
        "raw_context_tokens",
        "selected_pre_compression_tokens",
        "selected_post_compression_tokens",
        "compression_ratio",
        "tokens_avoided",
        "memory_tokens",
        "conversation_tokens",
        "tool_rag_tokens",
        "compressed_block_count",
        "compression_failure_count",
    ):
        assert key in built.metrics


def test_trace_metrics_do_not_include_sensitive_context_bodies(monkeypatch):
    monkeypatch.setattr(context.long_term, "retrieve_relevant", lambda *args, **kwargs: [])
    secret = "sk-live-secret-token"

    built = _build(monkeypatch, prior_context=secret, conversation=secret)

    assert secret not in str(built.metrics)


def test_baseline_vs_optimized_preserves_required_evidence():
    for fixture in context.representative_context_fixtures():
        result = context.measure_context_fixture(fixture, optimize=True)
        assert result.critical_evidence_preserved is True, fixture.name
        assert result.mandatory_context_preserved is True, fixture.name
        assert result.route_unchanged is True, fixture.name


def test_optimized_context_uses_no_more_than_baseline_tokens():
    for fixture in context.representative_context_fixtures():
        result = context.measure_context_fixture(fixture, optimize=True)
        assert result.optimized_tokens <= result.baseline_tokens, fixture.name


def test_phase6b_rolling_summary_regression_marker():
    conversation = (
        "Rolling summary of older conversation:\nCurrent goal: keep going\n"
        "Recent turns kept verbatim:\nUser: continue"
    )

    assert context._rolling_summary_tokens(conversation) > 0


def test_phase6c_context_selection_regression(monkeypatch):
    monkeypatch.setattr(
        context.long_term,
        "retrieve_relevant",
        lambda *args, **kwargs: [
            _memory("pin", "pinned_decision", "Pinned decision", importance=5),
            _memory("sem", "semantic", "Semantic memory"),
        ]
        if kwargs["kind"] in {"pinned_decision", "semantic"}
        else [],
    )

    built = _build(monkeypatch)

    assert built.metrics["selected_memory_ids"][0] == "pin"


def test_phase1_to_5_action_safety_regression():
    read = decide(FileIOTool(), {"action": "read", "path": "notes.txt", "content": None})
    write = decide(FileIOTool(), {"action": "write", "path": "notes.txt", "content": "x"})

    assert read.decision is PolicyDecisionType.ALLOW
    assert write.decision is PolicyDecisionType.REQUIRE_APPROVAL
    assert curation.normalized_hash("Postgres is durable.") == curation.normalized_hash("postgres is durable")


def test_sketch_memory_retrieval_failure_omits_optional_memory(monkeypatch):
    owner_id = _owner("sketch-fail-open")
    task_id = _task(owner_id)
    captured = {}

    def retrieval_unavailable(*args, **kwargs):
        raise RuntimeError("chroma unavailable")

    def fake_structured(prompt, response_model, **kwargs):
        captured["prompt"] = prompt
        completion = SimpleNamespace(
            model="test-model",
            usage=SimpleNamespace(
                prompt_tokens=1,
                completion_tokens=1,
                prompt_tokens_details=SimpleNamespace(cached_tokens=0),
            ),
        )
        return SketchOutput(outline=["continue"], confidence=5, reasoning="test"), completion

    monkeypatch.setattr(nodes.long_term, "retrieve_relevant", retrieval_unavailable)
    monkeypatch.setattr(nodes, "_tool_descriptions", lambda: "- test_tool")
    monkeypatch.setattr(nodes, "structured_complete", fake_structured)
    monkeypatch.setattr(nodes.cost, "record_llm_call", lambda *args, **kwargs: None)

    nodes.sketch_node({"task_id": task_id})

    assert "(no relevant past memories)" in captured["prompt"]
    assert "chroma unavailable" not in captured["prompt"]
