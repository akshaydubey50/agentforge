import uuid
from types import SimpleNamespace

from sqlmodel import select

from agentsys import context
from agentsys.config import settings
from agentsys.db.models import MemoryEntry, Subtask, SubtaskStatus, Task, TraceSpan, User
from agentsys.db.session import get_session, init_db
from agentsys.graph import nodes
from agentsys.graph.schemas import NextStepDecision
from agentsys.memory import long_term
from agentsys.memory.long_term import RetrievedMemory
from conftest import get_test_owner_id


def setup_module() -> None:
    init_db()


def _memory(mid: str, kind: str, content: str, *, importance: int = 3, score: float = 0.7) -> RetrievedMemory:
    return RetrievedMemory(
        id=mid,
        content=content,
        kind=kind,
        importance=importance,
        similarity=score,
        weighted_score=score,
    )


def test_context_builder_prioritizes_pinned_and_preferences(monkeypatch):
    by_kind = {
        "semantic": [_memory("sem", "semantic", "Generic lesson.", score=0.99)],
        "pinned_decision": [_memory("pin", "pinned_decision", "Postgres remains durable truth.", importance=5, score=0.7)],
        "preference": [_memory("pref", "preference", "Keep updates concise.", score=0.8)],
    }

    def fake_retrieve(query, *, owner_id, k, kind=None, similarity_floor=0.3):
        assert owner_id == "owner-1"
        return by_kind.get(kind, [])

    monkeypatch.setattr(context.long_term, "retrieve_relevant", fake_retrieve)
    monkeypatch.setattr(settings, "agent_step_context_budget_tokens", 10_000)
    monkeypatch.setattr(settings, "agent_step_reserved_output_tokens", 0)
    monkeypatch.setattr(settings, "agent_step_memory_budget_tokens", 500)
    monkeypatch.setattr(settings, "agent_step_memory_max_items", 6)

    built = context.build_agent_step_context(
        task_id="task-1",
        owner_id="owner-1",
        request_text="build context",
        conversation="",
        plan="ship phase 6c",
        prior_context="",
        dead_calls="",
        tool_descriptions="tools",
        steps_taken=0,
        steps_remaining=12,
    )

    lines = built.memory_context.splitlines()
    assert lines[0].startswith("- [pinned_decision")
    assert lines[1].startswith("- [preference")
    assert lines[2].startswith("- [semantic")
    assert built.metrics["selected_memory_ids"] == ["pin", "pref", "sem"]


def test_context_builder_drops_memory_over_budget(monkeypatch):
    monkeypatch.setattr(
        context.long_term,
        "retrieve_relevant",
        lambda *args, **kwargs: [
            _memory("m1", kwargs["kind"], "first"),
            _memory("m2", kwargs["kind"], "second"),
        ]
        if kwargs["kind"] == "semantic"
        else [],
    )
    monkeypatch.setattr(context, "estimate_tokens", lambda text, model=None: 10 if text.startswith("- [") else 1)
    monkeypatch.setattr(settings, "agent_step_context_budget_tokens", 1_000)
    monkeypatch.setattr(settings, "agent_step_reserved_output_tokens", 0)
    monkeypatch.setattr(settings, "agent_step_memory_budget_tokens", 15)
    monkeypatch.setattr(settings, "agent_step_memory_max_items", 6)

    built = context.build_agent_step_context(
        task_id="task-1",
        owner_id="owner-1",
        request_text="request",
        conversation="",
        plan="plan",
        prior_context="",
        dead_calls="",
        tool_descriptions="tools",
        steps_taken=0,
        steps_remaining=12,
    )

    assert built.metrics["retrieved_memory_count"] == 2
    assert built.metrics["selected_memory_count"] == 1
    assert built.metrics["dropped_memory_count"] == 1
    assert built.metrics["selected_context_tokens"] <= built.metrics["usable_input_budget_tokens"]


def test_context_builder_preserves_mandatory_context_under_token_pressure(monkeypatch):
    def should_not_retrieve(*args, **kwargs):
        raise AssertionError("optional memory retrieval should not run")

    monkeypatch.setattr(context.long_term, "retrieve_relevant", should_not_retrieve)
    monkeypatch.setattr(context, "estimate_tokens", lambda text, model=None: 50)
    monkeypatch.setattr(settings, "agent_step_context_budget_tokens", 10)
    monkeypatch.setattr(settings, "agent_step_reserved_output_tokens", 0)
    monkeypatch.setattr(settings, "agent_step_memory_budget_tokens", 500)

    conversation = (
        "Rolling summary of older conversation:\nCurrent goal: finish phase 6c\n"
        "Recent turns kept verbatim:\nUser (follow-up): keep it small"
    )
    built = context.build_agent_step_context(
        task_id="task-1",
        owner_id="owner-1",
        request_text="current request",
        conversation=conversation,
        plan="current plan",
        prior_context="prior evidence",
        dead_calls="dead-call warning",
        tool_descriptions="tools",
        steps_taken=1,
        steps_remaining=11,
    )

    assert built.conversation == conversation
    assert built.plan == "current plan"
    assert built.prior_context == "prior evidence"
    assert built.dead_calls == "dead-call warning"
    assert built.memory_context == "(no selected durable memory)"
    assert built.metrics["memory_skip_reason"] == "budget_exhausted"
    assert built.metrics["over_budget"] is True


def test_context_builder_memory_failure_fails_open(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("chroma down")

    monkeypatch.setattr(context.long_term, "retrieve_relevant", boom)
    monkeypatch.setattr(settings, "agent_step_context_budget_tokens", 10_000)
    monkeypatch.setattr(settings, "agent_step_reserved_output_tokens", 0)
    monkeypatch.setattr(settings, "agent_step_memory_budget_tokens", 500)

    built = context.build_agent_step_context(
        task_id="task-1",
        owner_id="owner-1",
        request_text="request",
        conversation="",
        plan="plan",
        prior_context="",
        dead_calls="",
        tool_descriptions="tools",
        steps_taken=0,
        steps_remaining=12,
    )

    assert built.memory_context == "(memory retrieval unavailable for this step)"
    assert built.metrics["memory_error"] == "RuntimeError"


def test_context_builder_skips_memory_when_protected_context_exhausts_budget(monkeypatch):
    def should_not_retrieve(*args, **kwargs):
        raise AssertionError("memory retrieval should not run without optional budget")

    monkeypatch.setattr(context.long_term, "retrieve_relevant", should_not_retrieve)
    monkeypatch.setattr(context, "estimate_tokens", lambda text, model=None: 1_000)
    monkeypatch.setattr(settings, "agent_step_context_budget_tokens", 100)
    monkeypatch.setattr(settings, "agent_step_reserved_output_tokens", 0)
    monkeypatch.setattr(settings, "agent_step_memory_budget_tokens", 500)

    built = context.build_agent_step_context(
        task_id="task-1",
        owner_id="owner-1",
        request_text="request",
        conversation="",
        plan="plan",
        prior_context="",
        dead_calls="",
        tool_descriptions="tools",
        steps_taken=0,
        steps_remaining=12,
    )

    assert built.memory_context == "(no selected durable memory)"
    assert built.metrics["memory_skip_reason"] == "budget_exhausted"
    assert built.metrics["over_budget"] is True


def test_agent_step_prompt_receives_selected_memory(monkeypatch):
    init_db()
    owner_id = get_test_owner_id()
    with get_session() as session:
        task = Task(request_text="Use the durable source of truth decision.", owner_id=owner_id)
        session.add(task)
        session.add(
            TraceSpan(
                task_id=task.id,
                span_type="sketch",
                name="supervisor_sketch",
                input={},
                output={"outline": ["check the durable store decision"]},
            )
        )
        session.commit()
        session.refresh(task)
        task_id = task.id

    monkeypatch.setattr(
        context.long_term,
        "retrieve_relevant",
        lambda *args, **kwargs: [
            _memory("pin", "pinned_decision", "SECRET MEMORY BODY: Postgres remains durable.", importance=5)
        ]
        if kwargs["kind"] == "pinned_decision"
        else [],
    )
    monkeypatch.setattr(nodes, "_tool_descriptions", lambda: "- test_tool: available")
    monkeypatch.setattr(nodes, "_execute_subtask", lambda *args, **kwargs: None)
    monkeypatch.setattr(nodes.cost, "record_llm_call", lambda *args, **kwargs: None)
    captured = {}

    def fake_structured(prompt, response_model, **kwargs):
        captured["prompt"] = prompt
        usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=SimpleNamespace(cached_tokens=0))
        completion = SimpleNamespace(model="test-model", usage=usage)
        return (
            NextStepDecision(
                next_action="act",
                subtask_description="do one step",
                tool_name="none",
                tool_input_json="{}",
                rationale="test",
            ),
            completion,
        )

    monkeypatch.setattr(nodes, "structured_complete", fake_structured)

    result = nodes.agent_step_node({"task_id": task_id})

    assert result["route"] == "escalate"
    assert "Selected durable memory and constraints" in captured["prompt"]
    assert "SECRET MEMORY BODY: Postgres remains durable." in captured["prompt"]
    with get_session() as session:
        subtasks = session.exec(select(Subtask).where(Subtask.task_id == task_id)).all()
        step_span = session.exec(
            select(TraceSpan).where(TraceSpan.task_id == task_id, TraceSpan.span_type == "agent_step")
        ).first()
    assert subtasks[0].status == SubtaskStatus.RUNNING
    assert step_span.output["context"]["selected_memory_ids"] == ["pin"]
    assert step_span.output["context"]["selected_memory_kinds"] == ["pinned_decision"]
    assert "SECRET MEMORY BODY" not in str(step_span.output["context"])


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


def _owner(prefix: str) -> str:
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


def _memory_row(memory_id: str, owner_id: str, *, kind: str = "semantic", status: str | None = "active"):
    with get_session() as session:
        session.add(
            MemoryEntry(
                id=memory_id,
                owner_id=owner_id,
                kind=kind,
                content=f"content {memory_id}",
                importance=3,
                meta={"status": status} if status is not None else {},
            )
        )
        session.commit()


def test_retrieve_relevant_uses_postgres_as_truth_for_chroma_hits(monkeypatch):
    owner_a = _owner("phase6c-owner-a")
    owner_b = _owner("phase6c-owner-b")
    marker = uuid.uuid4().hex
    active = f"active-a-{marker}"
    other_owner = f"other-owner-{marker}"
    superseded = f"superseded-a-{marker}"
    inactive = f"inactive-a-{marker}"
    missing = f"missing-{marker}"
    _memory_row(active, owner_a)
    _memory_row(other_owner, owner_b)
    _memory_row(superseded, owner_a, status="superseded")
    _memory_row(inactive, owner_a, status="inactive")

    monkeypatch.setattr(long_term, "embed_texts", lambda texts: [[0.0]])
    monkeypatch.setattr(
        long_term,
        "get_memory_collection",
        lambda: _FakeCollection([missing, other_owner, superseded, inactive, active]),
    )

    results = long_term.retrieve_relevant("query", owner_id=owner_a, k=5, kind="semantic")

    assert [result.id for result in results] == [active]


def test_pinned_decision_still_requires_relevance(monkeypatch):
    owner_id = _owner("phase6c-pinned")
    memory_id = f"irrelevant-pin-{uuid.uuid4().hex}"
    _memory_row(memory_id, owner_id, kind="pinned_decision")

    monkeypatch.setattr(long_term, "embed_texts", lambda texts: [[0.0]])
    monkeypatch.setattr(long_term, "get_memory_collection", lambda: _FakeCollection([memory_id], distances=[0.95]))

    results = long_term.retrieve_relevant(
        "unrelated query",
        owner_id=owner_id,
        k=3,
        kind="pinned_decision",
        similarity_floor=0.25,
    )

    assert results == []


def test_context_selection_is_deterministic(monkeypatch):
    monkeypatch.setattr(
        context.long_term,
        "retrieve_relevant",
        lambda *args, **kwargs: [
            _memory("b", kwargs["kind"], "B", score=0.8),
            _memory("a", kwargs["kind"], "A", score=0.8),
        ]
        if kwargs["kind"] == "semantic"
        else [],
    )
    monkeypatch.setattr(settings, "agent_step_context_budget_tokens", 10_000)
    monkeypatch.setattr(settings, "agent_step_reserved_output_tokens", 0)
    monkeypatch.setattr(settings, "agent_step_memory_budget_tokens", 500)
    monkeypatch.setattr(settings, "agent_step_memory_max_items", 6)

    kwargs = dict(
        task_id="task-1",
        owner_id="owner-1",
        request_text="request",
        conversation="conversation",
        plan="plan",
        prior_context="prior",
        dead_calls="",
        tool_descriptions="tools",
        steps_taken=0,
        steps_remaining=12,
    )

    first = context.build_agent_step_context(**kwargs)
    second = context.build_agent_step_context(**kwargs)

    assert first.metrics["selected_memory_ids"] == ["a", "b"]
    assert second.metrics["selected_memory_ids"] == ["a", "b"]
