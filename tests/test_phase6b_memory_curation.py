import uuid
from datetime import datetime, timezone

from litellm.utils import type_to_response_format_param
from sqlmodel import select

from agentsys.db.models import MemoryEntry, Subtask, SubtaskStatus, Task, User
from agentsys.db.session import get_session, init_db
from agentsys.graph import nodes
from agentsys.graph.schemas import MemoryCandidate, MemoryCandidateBatch
from agentsys.memory import curation, long_term


def setup_module() -> None:
    init_db()


def _owner() -> str:
    marker = uuid.uuid4().hex
    with get_session() as session:
        user = User(
            google_sub=f"phase6b-{marker}",
            email=f"phase6b-{marker}@example.com",
            name="Phase 6B",
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


def _task(owner_id: str, request_text: str = "Deploy FastAPI") -> str:
    with get_session() as session:
        task = Task(request_text=request_text, owner_id=owner_id)
        session.add(task)
        session.commit()
        session.refresh(task)
        return task.id


def _subtask(task_id: str) -> str:
    with get_session() as session:
        subtask = Subtask(
            task_id=task_id,
            position=0,
            description="Try deployment",
            assigned_tool="file_io",
            status=SubtaskStatus.DONE,
            output="Deployment succeeded",
        )
        session.add(subtask)
        session.commit()
        session.refresh(subtask)
        return subtask.id


def _memory_rows(owner_id: str) -> list[MemoryEntry]:
    with get_session() as session:
        return session.exec(select(MemoryEntry).where(MemoryEntry.owner_id == owner_id)).all()


def _stub_index_success(monkeypatch):
    def fake_index(memory_id: str) -> None:
        with get_session() as session:
            entry = session.get(MemoryEntry, memory_id)
            meta = dict(entry.meta or {})
            meta["chroma_indexed"] = True
            entry.meta = meta
            entry.updated_at = datetime.now(timezone.utc)
            session.add(entry)
            session.commit()

    monkeypatch.setattr("agentsys.memory.long_term.index_memory", fake_index)


def test_memory_candidate_schema_closes_nested_source_object_for_openai():
    response_format = type_to_response_format_param(MemoryCandidateBatch)
    schema = response_format["json_schema"]["schema"]
    source_schema = schema["$defs"]["MemoryCandidate"]["properties"]["source"]

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["MemoryCandidate"]["additionalProperties"] is False
    assert schema["$defs"]["MemoryCandidateSource"]["additionalProperties"] is False
    assert schema["$defs"]["MemoryArtifactRef"]["additionalProperties"] is False
    assert source_schema["additionalProperties"] is False


def test_valid_candidate_accepted(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)

    result = curation.curate_task_memory(
        task_id,
        [MemoryCandidate(kind="semantic", content="External services in containers should not use localhost.", confidence=0.9)],
    )

    rows = _memory_rows(owner_id)
    assert result.stored_count == 1
    assert len(rows) == 1
    assert rows[0].kind == "semantic"


def test_malformed_candidate_ignored(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)

    result = curation.curate_task_memory(
        task_id,
        [MemoryCandidate(kind="semantic", content="Useful lesson", source={"subtask_ids": ["missing"]})],
    )

    assert result.ignored_count == 1
    assert _memory_rows(owner_id) == []


def test_empty_candidate_ignored(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)

    result = curation.curate_task_memory(task_id, [MemoryCandidate(kind="semantic", content="   ")])

    assert result.ignored_count == 1
    assert _memory_rows(owner_id) == []


def test_oversized_candidate_ignored(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)

    result = curation.curate_task_memory(
        task_id,
        [MemoryCandidate(kind="semantic", content="x" * 2_000, confidence=0.9)],
    )

    assert result.ignored_count == 1
    assert _memory_rows(owner_id) == []


def test_exact_duplicate_does_not_create_new_memory(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)
    first = MemoryCandidate(kind="semantic", content="Postgres is the durable source of truth.", confidence=0.9)
    second = MemoryCandidate(kind="semantic", content=" postgres is the durable source of truth ", confidence=0.9)

    curation.curate_task_memory(task_id, [first])
    result = curation.curate_task_memory(task_id, [second])

    rows = _memory_rows(owner_id)
    assert result.merged_count == 1
    assert len(rows) == 1
    assert (rows[0].meta or {})["reinforcement_count"] == 2


def test_duplicate_is_owner_scoped(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_a = _owner()
    owner_b = _owner()
    task_a = _task(owner_a)
    task_b = _task(owner_b)
    candidate = MemoryCandidate(kind="semantic", content="Postgres remains durable truth.", confidence=0.9)

    curation.curate_task_memory(task_a, [candidate])
    curation.curate_task_memory(task_b, [candidate])

    assert len(_memory_rows(owner_a)) == 1
    assert len(_memory_rows(owner_b)) == 1


def test_semantic_merge_conservative_behavior(monkeypatch):
    _stub_index_success(monkeypatch)
    monkeypatch.setattr("agentsys.memory.long_term.retrieve_relevant", lambda *args, **kwargs: [])
    owner_id = _owner()
    task_id = _task(owner_id)

    curation.curate_task_memory(
        task_id,
        [MemoryCandidate(kind="semantic", content="Postgres is the durable state store.", confidence=0.9)],
    )
    result = curation.curate_task_memory(
        task_id,
        [MemoryCandidate(kind="semantic", content="PostgreSQL remains our primary persistent state store.", confidence=0.9)],
    )

    assert result.stored_count == 1
    assert len(_memory_rows(owner_id)) == 2


def test_user_a_cannot_merge_user_b_memory(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_a = _owner()
    owner_b = _owner()
    task_a = _task(owner_a)
    task_b = _task(owner_b)

    curation.curate_task_memory(task_b, [MemoryCandidate(kind="semantic", content="Owner scoped memory.", confidence=0.9)])
    result = curation.curate_task_memory(task_a, [MemoryCandidate(kind="semantic", content="Owner scoped memory.", confidence=0.9)])

    assert result.stored_count == 1
    assert len(_memory_rows(owner_a)) == 1
    assert len(_memory_rows(owner_b)) == 1


def test_pinned_decision_stored_correctly(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)

    curation.curate_task_memory(
        task_id,
        [MemoryCandidate(kind="pinned_decision", content="Postgres remains the durable source of truth.", importance=2, confidence=0.95)],
    )

    row = _memory_rows(owner_id)[0]
    assert row.kind == "pinned_decision"
    assert row.importance == 5
    assert (row.meta or {})["status"] == "active"


def test_episodic_memory_references_correct_task(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)
    subtask_id = _subtask(task_id)

    curation.curate_task_memory(
        task_id,
        [MemoryCandidate(kind="episodic", content="Goal: deploy FastAPI. Outcome: succeeded after using external DB URL.", source={"subtask_ids": [subtask_id]}, confidence=0.9)],
    )

    row = _memory_rows(owner_id)[0]
    assert row.task_id == task_id
    assert (row.meta or {})["source_task_id"] == task_id
    assert (row.meta or {})["sources"][0]["subtask_ids"] == [subtask_id]


def test_artifact_reference_does_not_copy_full_artifact(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)
    artifact_body = "FULL ARTIFACT BODY " * 200

    curation.curate_task_memory(
        task_id,
        [
            MemoryCandidate(
                kind="artifact_reference",
                content="Deployment report artifact for the successful run.",
                source={"artifact_refs": [{"task_id": task_id, "path": "report.txt", "body": artifact_body}]},
                confidence=0.9,
            )
        ],
    )

    row = _memory_rows(owner_id)[0]
    assert artifact_body not in row.content
    assert artifact_body not in str(row.meta)
    assert (row.meta or {})["artifact_refs"][0]["path"] == "report.txt"


def test_memory_extraction_failure_does_not_fail_task(monkeypatch):
    owner_id = _owner()
    task_id = _task(owner_id)

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(nodes, "structured_complete", boom)

    nodes._reflect_and_save_memory(task_id, "request", [], "final answer")

    assert _memory_rows(owner_id) == []


def test_postgres_persists_before_chroma_indexing(monkeypatch):
    seen = {}

    def assert_row_exists(memory_id: str) -> None:
        with get_session() as session:
            entry = session.get(MemoryEntry, memory_id)
            seen["exists_before_index"] = entry is not None
            meta = dict(entry.meta or {})
            meta["chroma_indexed"] = True
            entry.meta = meta
            session.add(entry)
            session.commit()

    monkeypatch.setattr("agentsys.memory.long_term.index_memory", assert_row_exists)
    owner_id = _owner()
    task_id = _task(owner_id)

    curation.curate_task_memory(task_id, [MemoryCandidate(kind="semantic", content="Persist first.", confidence=0.9)])

    assert seen["exists_before_index"] is True


def test_chroma_indexing_failure_leaves_durable_memory_intact(monkeypatch):
    def fail_index(memory_id: str) -> None:
        with get_session() as session:
            entry = session.get(MemoryEntry, memory_id)
            meta = dict(entry.meta or {})
            meta["chroma_indexed"] = False
            meta["chroma_error"] = "forced failure"
            entry.meta = meta
            session.add(entry)
            session.commit()
        raise RuntimeError("chroma down")

    monkeypatch.setattr("agentsys.memory.long_term.index_memory", fail_index)
    owner_id = _owner()
    task_id = _task(owner_id)

    result = curation.curate_task_memory(task_id, [MemoryCandidate(kind="semantic", content="Durable despite Chroma.", confidence=0.9)])

    row = _memory_rows(owner_id)[0]
    assert result.index_failed_count == 1
    assert row.content == "Durable despite Chroma."
    assert (row.meta or {})["chroma_indexed"] is False


def test_explicit_same_owner_supersession_marks_old_memory(monkeypatch):
    _stub_index_success(monkeypatch)
    owner_id = _owner()
    task_id = _task(owner_id)
    curation.curate_task_memory(
        task_id,
        [MemoryCandidate(kind="pinned_decision", content="Use Redis as durable truth.", confidence=0.9)],
    )
    old = _memory_rows(owner_id)[0]

    curation.curate_task_memory(
        task_id,
        [
            MemoryCandidate(
                kind="pinned_decision",
                content="Postgres is the durable source of truth.",
                confidence=0.95,
                source={"supersedes_memory_id": old.id},
            )
        ],
    )

    with get_session() as session:
        refreshed = session.get(MemoryEntry, old.id)
    assert (refreshed.meta or {})["status"] == "superseded"
    assert (refreshed.meta or {})["superseded_by"]


def test_pinned_decision_not_pruned_as_low_value(monkeypatch):
    deleted = []

    class FakeCollection:
        def delete(self, ids):
            deleted.extend(ids)

    monkeypatch.setattr(long_term, "get_memory_collection", lambda: FakeCollection())
    owner_id = _owner()
    task_id = _task(owner_id)
    with get_session() as session:
        pinned = MemoryEntry(
            owner_id=owner_id,
            task_id=task_id,
            kind="pinned_decision",
            content="Postgres remains durable truth.",
            importance=1,
            meta={"scope": "user"},
        )
        low = MemoryEntry(
            owner_id=owner_id,
            task_id=task_id,
            kind="semantic",
            content="Low-value semantic memory.",
            importance=1,
            meta={"scope": "user"},
        )
        session.add(pinned)
        session.add(low)
        session.commit()
        pinned_id = pinned.id
        low_id = low.id

    dropped = long_term.prune_low_value_memories(owner_id, keep_top_n=1)

    with get_session() as session:
        assert session.get(MemoryEntry, pinned_id) is not None
        assert session.get(MemoryEntry, low_id) is None
    assert dropped == 1
    assert deleted == [low_id]
