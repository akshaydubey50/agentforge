import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlmodel import select

from agentsys.db.models import MemoryEntry, Task, TaskMessage, TraceSpan, User
from agentsys.db.session import get_session, init_db
from agentsys.graph import nodes
from agentsys.graph.schemas import RollingConversationSummary


def setup_module() -> None:
    init_db()


def _owner() -> str:
    marker = uuid.uuid4().hex
    with get_session() as session:
        user = User(
            google_sub=f"phase6b-summary-{marker}",
            email=f"phase6b-summary-{marker}@example.com",
            name="Phase 6B Summary",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


def _task(owner_id: str, request_text: str = "Initial goal") -> Task:
    with get_session() as session:
        task = Task(request_text=request_text, owner_id=owner_id)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task


def _add_turns(task_id: str, count: int, *, start: datetime | None = None, prefix: str = "turn") -> None:
    base = start or datetime.now(timezone.utc)
    with get_session() as session:
        for i in range(count):
            answer_at = base + timedelta(seconds=i * 2 + 1)
            user_at = base + timedelta(seconds=i * 2 + 2)
            session.add(
                TraceSpan(
                    task_id=task_id,
                    span_type="synthesize",
                    name="supervisor_synthesize",
                    input={},
                    output={"final_answer": f"{prefix} assistant answer {i} " + ("detail " * 40)},
                    started_at=answer_at,
                )
            )
            session.add(
                TaskMessage(
                    task_id=task_id,
                    content=f"{prefix} user follow-up {i} " + ("detail " * 40),
                    created_at=user_at,
                )
            )
        session.commit()


def _reload_task(task_id: str) -> Task:
    with get_session() as session:
        return session.get(Task, task_id)


def _fake_completion():
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, prompt_tokens_details=SimpleNamespace(cached_tokens=0))
    return SimpleNamespace(model="test-model", usage=usage)


def _stub_summary(monkeypatch, summary: RollingConversationSummary, prompts: list[str] | None = None):
    def fake_structured(prompt, response_model, **kwargs):
        if prompts is not None:
            prompts.append(prompt)
        return summary, _fake_completion()

    monkeypatch.setattr(nodes, "structured_complete", fake_structured)
    monkeypatch.setattr(nodes.cost, "record_llm_call", lambda *args, **kwargs: None)


def test_below_threshold_no_summary_call(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 100_000)
    task = _task(_owner())
    _add_turns(task.id, 2)

    def fail_if_called(*args, **kwargs):
        raise AssertionError("summary should not run")

    monkeypatch.setattr(nodes, "structured_complete", fail_if_called)

    history = nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert "Rolling summary" not in history
    assert "user follow-up" in history


def test_above_threshold_summary_triggered(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    _stub_summary(monkeypatch, RollingConversationSummary(current_goal="Continue the deployment."))
    task = _task(_owner())
    _add_turns(task.id, 4)

    history = nodes._gather_conversation_history(task.id, _reload_task(task.id))

    stored = _reload_task(task.id)
    assert stored.rolling_summary["current_goal"] == "Continue the deployment."
    assert "Rolling summary of older conversation" in history


def test_recent_turns_remain_verbatim(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    _stub_summary(monkeypatch, RollingConversationSummary(completed_work=["Older turns summarized."]))
    task = _task(_owner())
    _add_turns(task.id, 5, prefix="recent-check")

    history = nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert "recent-check user follow-up 4" in history
    assert "recent-check assistant answer 4" in history


def test_older_turns_become_summary(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    _stub_summary(monkeypatch, RollingConversationSummary(completed_work=["Old deployment details were handled."]))
    task = _task(_owner())
    _add_turns(task.id, 5, prefix="old-check")

    history = nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert "Old deployment details were handled." in history
    assert "old-check user follow-up 0" not in history


def test_originals_remain_persisted(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    _stub_summary(monkeypatch, RollingConversationSummary(current_goal="Keep going."))
    task = _task(_owner())
    _add_turns(task.id, 4)

    nodes._gather_conversation_history(task.id, _reload_task(task.id))

    with get_session() as session:
        messages = session.exec(select(TaskMessage).where(TaskMessage.task_id == task.id)).all()
        synth = session.exec(select(TraceSpan).where(TraceSpan.task_id == task.id, TraceSpan.span_type == "synthesize")).all()
    assert len(messages) == 4
    assert len(synth) == 4


def test_existing_summary_updates_incrementally(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    prompts: list[str] = []
    _stub_summary(monkeypatch, RollingConversationSummary(current_goal="First summary."), prompts)
    task = _task(_owner())
    _add_turns(task.id, 4, prefix="first")

    nodes._gather_conversation_history(task.id, _reload_task(task.id))
    first_version = _reload_task(task.id).rolling_summary_version

    _stub_summary(monkeypatch, RollingConversationSummary(current_goal="Second summary."), prompts)
    _add_turns(task.id, 2, start=datetime.now(timezone.utc) + timedelta(minutes=5), prefix="second")
    nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert _reload_task(task.id).rolling_summary_version == first_version + 1
    assert "First summary." in prompts[-1]


def test_previous_summary_included_in_next_update(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    prompts: list[str] = []
    _stub_summary(monkeypatch, RollingConversationSummary(current_goal="Previous goal."), prompts)
    task = _task(_owner())
    _add_turns(task.id, 4, prefix="prev")
    nodes._gather_conversation_history(task.id, _reload_task(task.id))

    _stub_summary(monkeypatch, RollingConversationSummary(current_goal="Updated goal."), prompts)
    _add_turns(task.id, 2, start=datetime.now(timezone.utc) + timedelta(minutes=10), prefix="next")
    nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert "Previous goal." in prompts[-1]


def test_summary_failure_preserves_old_summary(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    task = _task(_owner())
    _add_turns(task.id, 4)
    with get_session() as session:
        stored = session.get(Task, task.id)
        stored.rolling_summary = {"current_goal": "Existing summary."}
        stored.rolling_summary_version = 1
        session.add(stored)
        session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(nodes, "structured_complete", boom)

    history = nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert "Existing summary." in history
    assert _reload_task(task.id).rolling_summary["current_goal"] == "Existing summary."


def test_summary_failure_does_not_break_conversation(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    task = _task(_owner())
    _add_turns(task.id, 4)
    monkeypatch.setattr(nodes, "structured_complete", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    history = nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert "continued conversation" in history
    assert "Recent turns kept verbatim" in history


def test_user_a_summary_cannot_use_user_b_messages(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 100_000)
    owner_a = _owner()
    owner_b = _owner()
    task_a = _task(owner_a, "Owner A original")
    task_b = _task(owner_b, "Owner B original")
    _add_turns(task_a.id, 2, prefix="owner-a")
    _add_turns(task_b.id, 2, prefix="owner-b")

    history = nodes._gather_conversation_history(task_a.id, _reload_task(task_a.id))

    assert "owner-a" in history
    assert "owner-b" not in history


def test_token_count_drops_after_summarization(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    _stub_summary(monkeypatch, RollingConversationSummary(current_goal="Short summary."))
    task = _task(_owner())
    _add_turns(task.id, 8, prefix="token-drop")
    turns = nodes._conversation_turns(task.id, _reload_task(task.id))
    before = nodes._estimate_tokens(nodes._full_conversation_block(turns))

    history = nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert nodes._estimate_tokens(history) < before


def test_pinned_decisions_not_deleted_by_summary_update(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    _stub_summary(monkeypatch, RollingConversationSummary(current_goal="Keep going."))
    owner_id = _owner()
    task = _task(owner_id)
    with get_session() as session:
        memory = MemoryEntry(
            owner_id=owner_id,
            task_id=task.id,
            kind="pinned_decision",
            content="Postgres remains the durable source of truth.",
            importance=5,
            meta={"scope": "user", "status": "active"},
        )
        session.add(memory)
        session.commit()
        memory_id = memory.id
    _add_turns(task.id, 4)

    nodes._gather_conversation_history(task.id, _reload_task(task.id))

    with get_session() as session:
        assert session.get(MemoryEntry, memory_id) is not None


def test_empty_summary_output_rejected(monkeypatch):
    monkeypatch.setattr(nodes.settings, "conversation_summary_trigger_tokens", 20)
    monkeypatch.setattr(nodes.settings, "conversation_recent_turns", 2)
    _stub_summary(monkeypatch, RollingConversationSummary())
    task = _task(_owner())
    _add_turns(task.id, 4)

    history = nodes._gather_conversation_history(task.id, _reload_task(task.id))

    assert _reload_task(task.id).rolling_summary is None
    assert "Rolling summary of older conversation" not in history
    assert "Recent turns kept verbatim" in history
