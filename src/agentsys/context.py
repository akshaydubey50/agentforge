from __future__ import annotations

from dataclasses import dataclass, field

import tiktoken

from agentsys.config import settings
from agentsys.memory import long_term
from agentsys.memory.long_term import RetrievedMemory

_TOKEN_ENCODING = None

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


@dataclass(frozen=True)
class ContextBlock:
    name: str
    text: str
    priority: int
    protected: bool = False
    metadata: dict = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class AgentStepContext:
    conversation: str
    plan: str
    prior_context: str
    dead_calls: str
    memory_context: str
    metrics: dict


def estimate_tokens(text: str, *, model: str | None = None) -> int:
    """Approximate prompt tokens for selection decisions.

    Provider usage in LlmCall remains ground truth after a model call. This is
    only a pre-call budget estimate, so it falls back to a rough character
    count rather than failing context assembly.
    """
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
) -> tuple[list[ContextBlock], int]:
    selected: list[ContextBlock] = []
    used = 0
    if max_items <= 0:
        return selected, used
    for memory in sorted(memories, key=_rank_key):
        if len(selected) >= max_items:
            break
        text = _format_memory(memory)
        tokens = estimate_tokens(text)
        if token_budget <= 0 or used + tokens > token_budget:
            continue
        selected.append(
            ContextBlock(
                name=f"memory:{memory.kind}",
                text=text,
                priority=_KIND_PRIORITY.get(memory.kind, 9),
                metadata={"id": memory.id, "kind": memory.kind, "tokens": tokens},
            )
        )
        used += tokens
    return selected, used


def _rolling_summary_tokens(conversation: str) -> int:
    if _ROLLING_SUMMARY_MARKER not in conversation:
        return 0
    start = conversation.find(_ROLLING_SUMMARY_MARKER)
    end = conversation.find(_RECENT_TURNS_MARKER, start)
    summary_text = conversation[start:] if end == -1 else conversation[start:end]
    return estimate_tokens(summary_text)


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
) -> AgentStepContext:
    model_context_window = max(0, settings.agent_step_context_budget_tokens)
    reserved_output_tokens = max(0, settings.agent_step_reserved_output_tokens)
    input_budget = max(
        0,
        model_context_window - reserved_output_tokens,
    )
    protected_text = "\n".join(
        [
            tool_descriptions,
            request_text,
            conversation,
            plan,
            prior_context,
            dead_calls,
            f"steps_taken={steps_taken}",
            f"steps_remaining={steps_remaining}",
        ]
    )
    protected_tokens = estimate_tokens(protected_text)
    available_tokens = max(0, input_budget - protected_tokens)
    memory_budget = min(max(0, settings.agent_step_memory_budget_tokens), available_tokens)
    conversation_tokens = estimate_tokens(conversation)
    rolling_summary_tokens = _rolling_summary_tokens(conversation)

    metrics = {
        "task_id": task_id,
        "model_context_window_tokens": model_context_window,
        "reserved_output_tokens": reserved_output_tokens,
        "usable_input_budget_tokens": input_budget,
        "protected_tokens": protected_tokens,
        "selected_context_tokens": protected_tokens,
        "conversation_tokens": conversation_tokens,
        "rolling_summary_tokens": rolling_summary_tokens,
        "recent_conversation_tokens": max(0, conversation_tokens - rolling_summary_tokens),
        "available_optional_tokens": available_tokens,
        "memory_budget_tokens": memory_budget,
        "retrieved_memory_count": 0,
        "selected_memory_count": 0,
        "dropped_memory_count": 0,
        "selected_memory_ids": [],
        "selected_memory_kinds": [],
        "selected_memory_tokens": 0,
        "over_budget": protected_tokens > input_budget,
    }

    if memory_budget <= 0:
        metrics["memory_skip_reason"] = "budget_exhausted"
        return AgentStepContext(
            conversation=conversation,
            plan=plan,
            prior_context=prior_context,
            dead_calls=dead_calls,
            memory_context="(no selected durable memory)",
            metrics=metrics,
        )

    try:
        memories = _retrieve_candidates(
            _memory_query(request_text, plan, prior_context), owner_id=owner_id
        )
    except Exception as exc:  # noqa: BLE001
        metrics["memory_error"] = type(exc).__name__
        return AgentStepContext(
            conversation=conversation,
            plan=plan,
            prior_context=prior_context,
            dead_calls=dead_calls,
            memory_context="(memory retrieval unavailable for this step)",
            metrics=metrics,
        )

    selected, selected_tokens = _select_memories(
        memories,
        token_budget=memory_budget,
        max_items=max(0, settings.agent_step_memory_max_items),
    )
    metrics["retrieved_memory_count"] = len(memories)
    metrics["selected_memory_count"] = len(selected)
    metrics["dropped_memory_count"] = max(0, len(memories) - len(selected))
    metrics["selected_memory_ids"] = [block.metadata["id"] for block in selected]
    metrics["selected_memory_kinds"] = [block.metadata["kind"] for block in selected]
    metrics["selected_memory_tokens"] = selected_tokens
    metrics["selected_context_tokens"] = protected_tokens + selected_tokens

    memory_context = (
        "\n".join(block.text for block in selected)
        if selected
        else "(no selected durable memory)"
    )
    return AgentStepContext(
        conversation=conversation,
        plan=plan,
        prior_context=prior_context,
        dead_calls=dead_calls,
        memory_context=memory_context,
        metrics=metrics,
    )
