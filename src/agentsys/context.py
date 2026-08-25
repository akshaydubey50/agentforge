from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

import tiktoken

from agentsys import pricing
from agentsys.config import settings
from agentsys.memory import long_term
from agentsys.memory.long_term import RetrievedMemory

_TOKEN_ENCODING = None

COMPRESSION_PROTECTED = "protected"
COMPRESSION_COMPRESSIBLE = "compressible"
COMPRESSION_OPTIONAL = "optional"

UNKNOWN_CONTEXT_WINDOW_TOKENS = 16_000
UNKNOWN_OUTPUT_RESERVE_TOKENS = 2_000

_KIND_PRIORITY = {
    "pinned_decision": 0,
    "preference": 1,
    "semantic": 2,
    "fact": 2,
    "episodic": 3,
    "artifact_reference": 4,
}

_ROLLING_SUMMARY_MARKER = "Rolling summary of older conversation:"
_RECENT_TURNS_MARKER = "Recent turns kept verbatim:"
_ERROR_LINE = re.compile(
    r"(error|failed|failure|exception|traceback|fatal|denied|timeout|not found|assert)",
    re.IGNORECASE,
)
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ModelContextProfile:
    model: str
    context_window_tokens: int
    default_output_reserve_tokens: int


_MODEL_CONTEXT_PROFILES = {
    "gpt-4o-mini": ModelContextProfile("gpt-4o-mini", 128_000, 2_000),
    "gpt-4o": ModelContextProfile("gpt-4o", 128_000, 4_000),
    "gpt-4.1-mini": ModelContextProfile("gpt-4.1-mini", 1_000_000, 4_000),
    "gpt-4.1": ModelContextProfile("gpt-4.1", 1_000_000, 4_000),
    "o3-mini": ModelContextProfile("o3-mini", 200_000, 8_000),
    "o3": ModelContextProfile("o3", 200_000, 8_000),
    "claude-3-5-haiku": ModelContextProfile("claude-3-5-haiku", 200_000, 4_000),
    "claude-3-5-sonnet": ModelContextProfile("claude-3-5-sonnet", 200_000, 8_000),
    "claude-3-7-sonnet": ModelContextProfile("claude-3-7-sonnet", 200_000, 8_000),
}


@dataclass(frozen=True)
class ContextBlock:
    name: str
    text: str
    priority: int
    protected: bool = False
    required: bool = False
    compression: str = COMPRESSION_OPTIONAL
    metadata: dict = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass(frozen=True)
class CompressionResult:
    block: ContextBlock
    original_tokens: int
    compressed_tokens: int
    compressed: bool
    failed: bool = False
    reference: str | None = None

    @property
    def tokens_avoided(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)


@dataclass
class AgentStepContext:
    conversation: str
    plan: str
    prior_context: str
    dead_calls: str
    memory_context: str
    metrics: dict
    capacity_error: str | None = None


@dataclass(frozen=True)
class ContextBenchmarkFixture:
    name: str
    blocks: list[ContextBlock]
    expected_evidence: list[str]
    output_tokens: int = 300
    cached_tokens: int | None = None
    model: str = "openai/gpt-4o-mini"
    deterministic_route: str = "act"


@dataclass(frozen=True)
class ContextBenchmarkResult:
    scenario: str
    baseline_tokens: int
    optimized_tokens: int
    reduction_tokens: int
    reduction_ratio: float
    raw_available_tokens: int
    selected_pre_compression_tokens: int
    selected_post_compression_tokens: int
    output_tokens: int
    cached_tokens: int | None
    memory_tokens: int
    conversation_tokens: int
    summary_tokens: int
    tool_rag_tokens: int
    cost_estimate_usd: float
    critical_evidence_preserved: bool
    mandatory_context_preserved: bool
    route_unchanged: bool

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "baseline_tokens": self.baseline_tokens,
            "optimized_tokens": self.optimized_tokens,
            "reduction_tokens": self.reduction_tokens,
            "reduction_ratio": self.reduction_ratio,
            "raw_available_tokens": self.raw_available_tokens,
            "selected_pre_compression_tokens": self.selected_pre_compression_tokens,
            "selected_post_compression_tokens": self.selected_post_compression_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "memory_tokens": self.memory_tokens,
            "conversation_tokens": self.conversation_tokens,
            "summary_tokens": self.summary_tokens,
            "tool_rag_tokens": self.tool_rag_tokens,
            "cost_estimate_usd": self.cost_estimate_usd,
            "critical_evidence_preserved": self.critical_evidence_preserved,
            "mandatory_context_preserved": self.mandatory_context_preserved,
            "route_unchanged": self.route_unchanged,
        }


class DeterministicContextCompressor:
    """Small built-in compressor for Phase 6D."""

    def compress(self, block: ContextBlock, *, budget_tokens: int) -> CompressionResult:
        original_tokens = block.tokens
        if block.protected or block.compression == COMPRESSION_PROTECTED:
            return CompressionResult(block, original_tokens, original_tokens, compressed=False)
        if block.compression != COMPRESSION_COMPRESSIBLE:
            return CompressionResult(block, original_tokens, original_tokens, compressed=False)
        if original_tokens <= max(0, budget_tokens):
            return CompressionResult(block, original_tokens, original_tokens, compressed=False)

        try:
            text = _compress_text(block, budget_tokens=max(64, budget_tokens))
            reference = _block_reference(block)
            compressed_block = ContextBlock(
                name=block.name,
                text=text,
                priority=block.priority,
                protected=False,
                required=block.required,
                compression=block.compression,
                metadata={**block.metadata, "compressed": True, "reference": reference},
            )
            return CompressionResult(
                compressed_block,
                original_tokens,
                compressed_block.tokens,
                compressed=True,
                reference=reference,
            )
        except Exception:  # noqa: BLE001
            fallback = _bounded_fallback(block, budget_tokens=max(64, budget_tokens))
            return CompressionResult(
                fallback,
                original_tokens,
                fallback.tokens,
                compressed=True,
                failed=True,
                reference=_block_reference(block),
            )

    def retrieve_original(self, ref: str) -> str | None:
        return None


def estimate_tokens(text: str, *, model: str | None = None) -> int:
    """Approximate prompt tokens for pre-call selection decisions."""
    global _TOKEN_ENCODING
    try:
        if _TOKEN_ENCODING is None:
            model_name = (model or settings.llm_model).split("/", 1)[-1]
            try:
                _TOKEN_ENCODING = tiktoken.encoding_for_model(model_name)
            except Exception:  # noqa: BLE001
                _TOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")
        return len(_TOKEN_ENCODING.encode(text or ""))
    except Exception:  # noqa: BLE001
        return max(1, len(text or "") // 4)


def model_context_profile(model: str | None = None) -> ModelContextProfile:
    raw = model or settings.llm_model
    stripped = raw.split("/", 1)[-1].lower()
    for key in sorted(_MODEL_CONTEXT_PROFILES, key=len, reverse=True):
        if stripped == key or stripped.startswith(f"{key}-"):
            profile = _MODEL_CONTEXT_PROFILES[key]
            return ModelContextProfile(
                raw,
                profile.context_window_tokens,
                profile.default_output_reserve_tokens,
            )
    return ModelContextProfile(raw, UNKNOWN_CONTEXT_WINDOW_TOKENS, UNKNOWN_OUTPUT_RESERVE_TOKENS)


def model_context_window(model: str | None = None) -> int:
    return model_context_profile(model).context_window_tokens


def default_output_reserve(model: str | None = None) -> int:
    return model_context_profile(model).default_output_reserve_tokens


def _normalize_duplicate_text(text: str) -> str:
    return _SPACE.sub(" ", (text or "").strip().lower())


def _dedupe_key(block: ContextBlock) -> tuple[str, str] | None:
    meta = block.metadata or {}
    for key in ("memory_id", "id", "source_id", "artifact_id", "tool_result_ref", "chunk_id"):
        value = meta.get(key)
        if value:
            return key, str(value)
    normalized = _normalize_duplicate_text(block.text)
    if normalized:
        return "content", normalized
    return None


def deduplicate_context_blocks(blocks: list[ContextBlock]) -> tuple[list[ContextBlock], int]:
    """Remove only exact deterministic duplicates."""
    selected: list[ContextBlock] = []
    seen: set[tuple[str, str]] = set()
    removed = 0
    for block in blocks:
        key = _dedupe_key(block)
        if block.protected or key is None:
            selected.append(block)
            if key:
                seen.add(key)
            continue
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        selected.append(block)
    return selected, removed


def _block_reference(block: ContextBlock) -> str | None:
    meta = block.metadata or {}
    for key in ("artifact_path", "full_output_path", "source_id", "tool_result_ref", "chunk_id"):
        value = meta.get(key)
        if value:
            return str(value)
    text = block.text or ""
    match = re.search(r"(_artifacts/[A-Za-z0-9_.\-]+)", text)
    return match.group(1) if match else None


def _bounded_chars(budget_tokens: int) -> int:
    return max(240, budget_tokens * 4)


def _interesting_lines(text: str, *, max_lines: int = 8) -> list[str]:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return [line[:500] for line in lines if _ERROR_LINE.search(line)][:max_lines]


def _compress_text(block: ContextBlock, *, budget_tokens: int) -> str:
    text = block.text or ""
    max_chars = _bounded_chars(budget_tokens)
    if len(text) <= max_chars:
        return text

    ref = _block_reference(block)
    errors = _interesting_lines(text)
    preview_chars = max(160, max_chars // 3)
    tail_chars = max(160, max_chars // 3)
    parts = [
        (
            f"[compressed context block: {block.name}; "
            f"original_tokens={block.tokens}; original_chars={len(text)}]"
        )
    ]
    if ref:
        parts.append(f"Reference for full original: {ref}")
    if errors:
        parts.append("Key failure/error lines:")
        parts.extend(f"- {line}" for line in errors)
    parts.append("Preview:")
    parts.append(text[:preview_chars].strip())
    parts.append("Tail:")
    parts.append(text[-tail_chars:].strip())
    return "\n".join(part for part in parts if part)


def _bounded_fallback(block: ContextBlock, *, budget_tokens: int) -> ContextBlock:
    max_chars = _bounded_chars(budget_tokens)
    text = (block.text or "")[:max_chars]
    ref = _block_reference(block)
    suffix = (
        f"\n[compression failed; bounded excerpt only. Reference: {ref}]"
        if ref
        else "\n[compression failed; bounded excerpt only.]"
    )
    return ContextBlock(
        name=block.name,
        text=f"{text}{suffix}",
        priority=block.priority,
        required=block.required,
        compression=block.compression,
        metadata={**block.metadata, "compression_failed": True, "reference": ref},
    )


def compress_context_blocks(
    blocks: list[ContextBlock],
    *,
    budget_tokens: int,
    compressor: DeterministicContextCompressor | None = None,
) -> tuple[list[ContextBlock], list[CompressionResult]]:
    compressor = compressor or DeterministicContextCompressor()
    if not blocks:
        return [], []
    per_block_budget = max(64, budget_tokens // max(1, len(blocks)))
    compressed: list[ContextBlock] = []
    results: list[CompressionResult] = []
    for block in blocks:
        try:
            result = compressor.compress(block, budget_tokens=per_block_budget)
        except Exception:  # noqa: BLE001
            fallback = _bounded_fallback(block, budget_tokens=per_block_budget)
            result = CompressionResult(
                fallback,
                block.tokens,
                fallback.tokens,
                compressed=True,
                failed=True,
                reference=_block_reference(block),
            )
        compressed.append(result.block)
        results.append(result)
    return compressed, results


def _memory_query(request_text: str, plan: str, prior_context: str) -> str:
    return "\n".join(
        part
        for part in (
            f"Request: {request_text}",
            f"Current plan: {plan}",
            f"Recent task evidence: {prior_context[:1500]}",
        )
        if part.strip()
    )


def _retrieve_candidates(query: str, *, owner_id: str) -> list[RetrievedMemory]:
    candidates: list[RetrievedMemory] = []
    seen: set[str] = set()
    retrievals = (
        ("pinned_decision", 3, 0.25),
        ("preference", 2, 0.25),
        ("semantic", 4, 0.3),
        ("fact", 2, 0.3),
        ("episodic", 2, 0.35),
        ("artifact_reference", 2, 0.35),
    )
    for kind, k, floor in retrievals:
        for memory in long_term.retrieve_relevant(
            query, owner_id=owner_id, k=k, kind=kind, similarity_floor=floor
        ):
            if memory.id in seen:
                continue
            seen.add(memory.id)
            candidates.append(memory)
    return candidates


def _rank_key(memory: RetrievedMemory) -> tuple:
    return (
        _KIND_PRIORITY.get(memory.kind, 9),
        -memory.weighted_score,
        -memory.importance,
        memory.id,
    )


def _format_memory(memory: RetrievedMemory) -> str:
    return (
        f"- [{memory.kind}; importance={memory.importance}; "
        f"relevance={memory.weighted_score:.3f}] {memory.content}"
    )


def _select_memories(
    memories: list[RetrievedMemory], *, token_budget: int, max_items: int
) -> tuple[list[ContextBlock], int, int]:
    selected: list[ContextBlock] = []
    used = 0
    duplicate_removed = 0
    seen: set[tuple[str, str]] = set()
    if max_items <= 0:
        return selected, used, duplicate_removed
    for memory in sorted(memories, key=_rank_key):
        if len(selected) >= max_items:
            break
        text = _format_memory(memory)
        block = ContextBlock(
            name=f"memory:{memory.kind}",
            text=text,
            priority=_KIND_PRIORITY.get(memory.kind, 9),
            compression=COMPRESSION_OPTIONAL,
            metadata={
                "id": memory.id,
                "memory_id": memory.id,
                "kind": memory.kind,
                "source_type": "memory",
            },
        )
        key = _dedupe_key(block)
        if key and key in seen:
            duplicate_removed += 1
            continue
        tokens = block.tokens
        if token_budget <= 0 or used + tokens > token_budget:
            continue
        if key:
            seen.add(key)
        selected.append(
            ContextBlock(
                block.name,
                block.text,
                block.priority,
                metadata={**block.metadata, "tokens": tokens},
            )
        )
        used += tokens
    return selected, used, duplicate_removed


def _rolling_summary_tokens(conversation: str) -> int:
    if _ROLLING_SUMMARY_MARKER not in conversation:
        return 0
    start = conversation.find(_ROLLING_SUMMARY_MARKER)
    end = conversation.find(_RECENT_TURNS_MARKER, start)
    summary_text = conversation[start:] if end == -1 else conversation[start:end]
    return estimate_tokens(summary_text)


def _artifact_reference_count(text: str) -> int:
    return len(re.findall(r"_artifacts[/\\][A-Za-z0-9_.\-]+", text or ""))


def _agent_step_blocks(
    *,
    request_text: str,
    conversation: str,
    plan: str,
    prior_context: str,
    dead_calls: str,
    tool_descriptions: str,
    steps_taken: int,
    steps_remaining: int,
) -> tuple[list[ContextBlock], list[ContextBlock]]:
    protected = [
        ContextBlock(
            "tool_definitions",
            tool_descriptions,
            0,
            protected=True,
            required=True,
            compression=COMPRESSION_PROTECTED,
            metadata={"source_type": "tool_definitions"},
        ),
        ContextBlock(
            "current_goal",
            request_text,
            1,
            protected=True,
            required=True,
            compression=COMPRESSION_PROTECTED,
            metadata={"source_type": "current_goal"},
        ),
        ContextBlock(
            "planner_state",
            plan,
            2,
            protected=True,
            required=True,
            compression=COMPRESSION_PROTECTED,
            metadata={"source_type": "planner_state"},
        ),
        ContextBlock(
            "dead_calls",
            dead_calls,
            3,
            protected=True,
            required=True,
            compression=COMPRESSION_PROTECTED,
            metadata={"source_type": "dead_calls"},
        ),
        ContextBlock(
            "step_budget",
            f"steps_taken={steps_taken}\nsteps_remaining={steps_remaining}",
            4,
            protected=True,
            required=True,
            compression=COMPRESSION_PROTECTED,
            metadata={"source_type": "planner_state"},
        ),
    ]
    required_compressible = [
        ContextBlock(
            "conversation",
            conversation,
            5,
            required=True,
            compression=COMPRESSION_COMPRESSIBLE,
            metadata={"source_type": "conversation"},
        ),
        ContextBlock(
            "prior_context",
            prior_context,
            6,
            required=True,
            compression=COMPRESSION_COMPRESSIBLE,
            metadata={
                "source_type": "tool_rag",
                "artifact_references": _artifact_reference_count(prior_context),
            },
        ),
    ]
    return protected, required_compressible


def _selected_required_context(
    *,
    protected_blocks: list[ContextBlock],
    required_blocks: list[ContextBlock],
    input_budget: int,
    compressor: DeterministicContextCompressor | None,
) -> tuple[list[ContextBlock], list[CompressionResult], str | None]:
    protected_tokens = sum(block.tokens for block in protected_blocks)
    if protected_tokens > input_budget:
        return protected_blocks + required_blocks, [], "protected_context_exceeds_model_input_budget"

    required_tokens = sum(block.tokens for block in required_blocks)
    if protected_tokens + required_tokens <= input_budget:
        return protected_blocks + required_blocks, [], None

    remaining = max(0, input_budget - protected_tokens)
    compressed_required, compression_results = compress_context_blocks(
        required_blocks,
        budget_tokens=remaining,
        compressor=compressor,
    )
    selected = protected_blocks + compressed_required
    if sum(block.tokens for block in selected) > input_budget:
        return selected, compression_results, "mandatory_context_exceeds_model_input_budget_after_compression"
    return selected, compression_results, None


def build_agent_step_context(
    *,
    task_id: str,
    owner_id: str,
    request_text: str,
    conversation: str,
    plan: str,
    prior_context: str,
    dead_calls: str,
    tool_descriptions: str,
    steps_taken: int,
    steps_remaining: int,
    compressor: DeterministicContextCompressor | None = None,
) -> AgentStepContext:
    profile = model_context_profile(settings.llm_model)
    configured_context_budget = max(0, settings.agent_step_context_budget_tokens)
    model_context_limit = profile.context_window_tokens
    model_context_window = min(configured_context_budget, model_context_limit)
    reserved_output_tokens = max(0, settings.agent_step_reserved_output_tokens)
    input_budget = max(0, model_context_window - reserved_output_tokens)

    protected_blocks, required_blocks = _agent_step_blocks(
        request_text=request_text,
        conversation=conversation,
        plan=plan,
        prior_context=prior_context,
        dead_calls=dead_calls,
        tool_descriptions=tool_descriptions,
        steps_taken=steps_taken,
        steps_remaining=steps_remaining,
    )
    raw_required_tokens = sum(block.tokens for block in protected_blocks + required_blocks)
    protected_tokens = sum(block.tokens for block in protected_blocks)
    conversation_tokens = estimate_tokens(conversation)
    rolling_summary_tokens = _rolling_summary_tokens(conversation)
    tool_rag_tokens = estimate_tokens(prior_context)

    selected_required, compression_results, capacity_error = _selected_required_context(
        protected_blocks=protected_blocks,
        required_blocks=required_blocks,
        input_budget=input_budget,
        compressor=compressor,
    )
    selected_required_tokens = sum(block.tokens for block in selected_required)
    available_tokens = max(0, input_budget - selected_required_tokens)
    memory_budget = min(max(0, settings.agent_step_memory_budget_tokens), available_tokens)

    compressed_blocks = [result for result in compression_results if result.compressed]
    compression_failure_count = sum(1 for result in compression_results if result.failed)
    tokens_avoided = sum(result.tokens_avoided for result in compression_results)

    selected_conversation = next(
        (block.text for block in selected_required if block.name == "conversation"),
        conversation,
    )
    selected_prior_context = next(
        (block.text for block in selected_required if block.name == "prior_context"),
        prior_context,
    )

    metrics = {
        "task_id": task_id,
        "model": settings.llm_model,
        "model_context_window_tokens": model_context_limit,
        "configured_context_budget_tokens": configured_context_budget,
        "effective_context_window_tokens": model_context_window,
        "reserved_output_tokens": reserved_output_tokens,
        "usable_input_budget_tokens": input_budget,
        "raw_context_tokens": raw_required_tokens,
        "protected_tokens": protected_tokens,
        "selected_pre_compression_tokens": raw_required_tokens,
        "selected_post_compression_tokens": selected_required_tokens,
        "selected_context_tokens": selected_required_tokens,
        "compression_ratio": (
            round(selected_required_tokens / raw_required_tokens, 4) if raw_required_tokens else 1.0
        ),
        "tokens_avoided": tokens_avoided,
        "conversation_tokens": conversation_tokens,
        "rolling_summary_tokens": rolling_summary_tokens,
        "recent_conversation_tokens": max(0, conversation_tokens - rolling_summary_tokens),
        "summary_tokens": rolling_summary_tokens,
        "tool_rag_tokens": tool_rag_tokens,
        "memory_tokens": 0,
        "artifact_references": _artifact_reference_count(prior_context),
        "compressed_block_count": len(compressed_blocks),
        "compression_failure_count": compression_failure_count,
        "available_optional_tokens": available_tokens,
        "memory_budget_tokens": memory_budget,
        "retrieved_memory_count": 0,
        "selected_memory_count": 0,
        "dropped_memory_count": 0,
        "duplicate_context_removed_count": 0,
        "selected_memory_ids": [],
        "selected_memory_kinds": [],
        "selected_memory_tokens": 0,
        "active_memory_count": 0,
        "archived_memory_count": 0,
        "capacity_error": capacity_error,
        "over_budget": capacity_error is not None,
    }

    if capacity_error:
        metrics["memory_skip_reason"] = "budget_exhausted"
        return AgentStepContext(
            conversation=selected_conversation,
            plan=plan,
            prior_context=selected_prior_context,
            dead_calls=dead_calls,
            memory_context="(no selected durable memory)",
            metrics=metrics,
            capacity_error=capacity_error,
        )

    if memory_budget <= 0:
        metrics["memory_skip_reason"] = "budget_exhausted"
        return AgentStepContext(
            conversation=selected_conversation,
            plan=plan,
            prior_context=selected_prior_context,
            dead_calls=dead_calls,
            memory_context="(no selected durable memory)",
            metrics=metrics,
        )

    try:
        memories = _retrieve_candidates(
            _memory_query(request_text, plan, selected_prior_context), owner_id=owner_id
        )
    except Exception as exc:  # noqa: BLE001
        metrics["memory_error"] = type(exc).__name__
        return AgentStepContext(
            conversation=selected_conversation,
            plan=plan,
            prior_context=selected_prior_context,
            dead_calls=dead_calls,
            memory_context="(memory retrieval unavailable for this step)",
            metrics=metrics,
        )

    selected, selected_tokens, memory_duplicates = _select_memories(
        memories,
        token_budget=memory_budget,
        max_items=max(0, settings.agent_step_memory_max_items),
    )
    metrics["retrieved_memory_count"] = len(memories)
    metrics["active_memory_count"] = len(memories)
    metrics["selected_memory_count"] = len(selected)
    metrics["dropped_memory_count"] = max(0, len(memories) - len(selected))
    metrics["duplicate_context_removed_count"] += memory_duplicates
    metrics["selected_memory_ids"] = [block.metadata["id"] for block in selected]
    metrics["selected_memory_kinds"] = [block.metadata["kind"] for block in selected]
    metrics["selected_memory_tokens"] = selected_tokens
    metrics["memory_tokens"] = selected_tokens
    metrics["selected_context_tokens"] = selected_required_tokens + selected_tokens
    metrics["selected_post_compression_tokens"] = selected_required_tokens + selected_tokens
    metrics["compression_ratio"] = (
        round(metrics["selected_post_compression_tokens"] / raw_required_tokens, 4)
        if raw_required_tokens
        else 1.0
    )

    if selected:
        try:
            long_term.reinforce_memory_use(
                [block.metadata["id"] for block in selected],
                owner_id=owner_id,
            )
        except Exception as exc:  # noqa: BLE001
            metrics["memory_reinforcement_error"] = type(exc).__name__

    memory_context = (
        "\n".join(block.text for block in selected)
        if selected
        else "(no selected durable memory)"
    )
    return AgentStepContext(
        conversation=selected_conversation,
        plan=plan,
        prior_context=selected_prior_context,
        dead_calls=dead_calls,
        memory_context=memory_context,
        metrics=metrics,
    )


def representative_context_fixtures() -> list[ContextBenchmarkFixture]:
    repeated_turn = "User asked for deployment help. Assistant explained the same constraint. "
    large_log = "\n".join(
        [f"INFO test_{i} passed" for i in range(80)]
        + ["ERROR deployment failed: DATABASE_URL points at localhost"]
        + [f"INFO cleanup_{i} complete" for i in range(80)]
    )
    rag_payload = "\n".join(
        f"source {i}: hybrid retrieval chunk about tenant isolation and citation verification"
        for i in range(40)
    )
    return [
        ContextBenchmarkFixture(
            name="short_conversation",
            blocks=[
                ContextBlock(
                    "static_prompt",
                    "Follow safety and tool rules.",
                    0,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "static"},
                ),
                ContextBlock(
                    "current_goal",
                    "Summarize the previous answer.",
                    1,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "current_goal"},
                ),
                ContextBlock(
                    "conversation",
                    "User: thanks\nAssistant: You're welcome.",
                    2,
                    required=True,
                    compression=COMPRESSION_COMPRESSIBLE,
                    metadata={"source_type": "conversation"},
                ),
            ],
            expected_evidence=["Summarize the previous answer"],
        ),
        ContextBenchmarkFixture(
            name="long_conversation_with_summary",
            blocks=[
                ContextBlock(
                    "static_prompt",
                    "Follow safety and tool rules.",
                    0,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "static"},
                ),
                ContextBlock(
                    "current_goal",
                    "Continue the deployment debugging task.",
                    1,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "current_goal"},
                ),
                ContextBlock(
                    "conversation",
                    repeated_turn * 120 + "\nRecent turns kept verbatim:\nUser: keep Postgres as durable truth.",
                    2,
                    required=True,
                    compression=COMPRESSION_COMPRESSIBLE,
                    metadata={"source_type": "conversation"},
                ),
            ],
            expected_evidence=["Postgres as durable truth"],
        ),
        ContextBenchmarkFixture(
            name="memory_heavy_task",
            blocks=[
                ContextBlock(
                    "static_prompt",
                    "Follow safety and tool rules.",
                    0,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "static"},
                ),
                ContextBlock(
                    "current_goal",
                    "Plan a safe external action.",
                    1,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "current_goal"},
                ),
                ContextBlock(
                    "memory_required",
                    "Pinned: approval is required before external writes.",
                    2,
                    required=True,
                    compression=COMPRESSION_OPTIONAL,
                    metadata={"source_type": "memory", "memory_id": "pin-1"},
                ),
                ContextBlock(
                    "memory_duplicate",
                    "Pinned: approval is required before external writes.",
                    3,
                    compression=COMPRESSION_OPTIONAL,
                    metadata={"source_type": "memory", "memory_id": "pin-1"},
                ),
                ContextBlock(
                    "memory_low_value",
                    "Low value background " * 200,
                    9,
                    compression=COMPRESSION_OPTIONAL,
                    metadata={"source_type": "memory", "memory_id": "low-1"},
                ),
            ],
            expected_evidence=["approval is required"],
        ),
        ContextBenchmarkFixture(
            name="large_tool_result",
            blocks=[
                ContextBlock(
                    "static_prompt",
                    "Follow safety and tool rules.",
                    0,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "static"},
                ),
                ContextBlock(
                    "current_goal",
                    "Fix the failing deployment.",
                    1,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "current_goal"},
                ),
                ContextBlock(
                    "tool_log",
                    large_log,
                    5,
                    required=True,
                    compression=COMPRESSION_COMPRESSIBLE,
                    metadata={"source_type": "tool_rag", "artifact_path": "_artifacts/deploy_log.json"},
                ),
            ],
            expected_evidence=["DATABASE_URL points at localhost"],
        ),
        ContextBenchmarkFixture(
            name="rag_heavy_task",
            blocks=[
                ContextBlock(
                    "static_prompt",
                    "Follow safety and tool rules.",
                    0,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "static"},
                ),
                ContextBlock(
                    "current_goal",
                    "Answer using retrieved knowledge.",
                    1,
                    protected=True,
                    required=True,
                    compression=COMPRESSION_PROTECTED,
                    metadata={"source_type": "current_goal"},
                ),
                ContextBlock(
                    "rag_results",
                    rag_payload + "\nsource 41: required-memory preservation evidence",
                    5,
                    required=True,
                    compression=COMPRESSION_COMPRESSIBLE,
                    metadata={"source_type": "tool_rag", "chunk_id": "rag-heavy"},
                ),
            ],
            expected_evidence=["required-memory preservation evidence"],
        ),
    ]


def _source_tokens(blocks: list[ContextBlock], source_type: str) -> int:
    return sum(block.tokens for block in blocks if (block.metadata or {}).get("source_type") == source_type)


def measure_context_fixture(
    fixture: ContextBenchmarkFixture,
    *,
    optimize: bool = True,
    input_budget_tokens: int | None = None,
    compressor: DeterministicContextCompressor | None = None,
) -> ContextBenchmarkResult:
    blocks = list(fixture.blocks)
    baseline_tokens = sum(block.tokens for block in blocks)
    budget = input_budget_tokens or (
        model_context_window(fixture.model) - default_output_reserve(fixture.model)
    )
    selected_blocks = blocks
    removed = 0
    if optimize:
        selected_blocks, removed = deduplicate_context_blocks(selected_blocks)
        compressible = [
            block for block in selected_blocks if block.compression == COMPRESSION_COMPRESSIBLE
        ]
        protected_or_other = [
            block for block in selected_blocks if block.compression != COMPRESSION_COMPRESSIBLE
        ]
        remaining = max(0, budget - sum(block.tokens for block in protected_or_other))
        compressed, _compression_results = compress_context_blocks(
            compressible,
            budget_tokens=remaining,
            compressor=compressor,
        )
        selected_blocks = sorted(protected_or_other + compressed, key=lambda b: b.priority)
    optimized_tokens = sum(block.tokens for block in selected_blocks)
    selected_text = "\n".join(block.text for block in selected_blocks)
    evidence_preserved = all(evidence in selected_text for evidence in fixture.expected_evidence)
    mandatory_preserved = all(
        block.text in selected_text for block in blocks if block.protected and block.required
    )
    reduction_tokens = max(0, baseline_tokens - optimized_tokens)
    return ContextBenchmarkResult(
        scenario=fixture.name,
        baseline_tokens=baseline_tokens,
        optimized_tokens=optimized_tokens,
        reduction_tokens=reduction_tokens,
        reduction_ratio=round(reduction_tokens / baseline_tokens, 4) if baseline_tokens else 0.0,
        raw_available_tokens=budget,
        selected_pre_compression_tokens=baseline_tokens - removed,
        selected_post_compression_tokens=optimized_tokens,
        output_tokens=fixture.output_tokens,
        cached_tokens=fixture.cached_tokens,
        memory_tokens=_source_tokens(selected_blocks, "memory"),
        conversation_tokens=_source_tokens(selected_blocks, "conversation"),
        summary_tokens=sum(block.tokens for block in selected_blocks if block.name == "summary"),
        tool_rag_tokens=_source_tokens(selected_blocks, "tool_rag"),
        cost_estimate_usd=pricing.cost_for(
            fixture.model,
            optimized_tokens,
            fixture.output_tokens,
            fixture.cached_tokens,
        ),
        critical_evidence_preserved=evidence_preserved,
        mandatory_context_preserved=mandatory_preserved,
        route_unchanged=fixture.deterministic_route == "act",
    )


def run_representative_context_benchmark() -> list[dict]:
    return [
        measure_context_fixture(fixture, optimize=True, input_budget_tokens=500).to_dict()
        for fixture in representative_context_fixtures()
    ]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
