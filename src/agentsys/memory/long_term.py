import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlmodel import select

from agentsys.db.models import MemoryEntry
from agentsys.db.session import get_session
from agentsys.llm import embed_texts
from agentsys.memory.chroma_client import get_memory_collection


@dataclass
class RetrievedMemory:
    id: str
    content: str
    kind: str
    importance: int
    similarity: float
    weighted_score: float


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chroma_metadata(entry: MemoryEntry) -> dict:
    meta = entry.meta or {}
    return {
        "kind": entry.kind,
        "task_id": entry.task_id or "",
        "owner_id": entry.owner_id,
        "importance": entry.importance,
        "scope": str(meta.get("scope") or "user"),
        "normalized_hash": str(meta.get("normalized_hash") or ""),
    }


def _mark_index_status(memory_id: str, *, indexed: bool, error: str | None = None) -> None:
    with get_session() as session:
        entry = session.get(MemoryEntry, memory_id)
        if not entry:
            return
        meta = dict(entry.meta or {})
        meta["chroma_indexed"] = indexed
        if error:
            meta["chroma_error"] = error[:300]
        else:
            meta.pop("chroma_error", None)
        entry.meta = meta
        entry.updated_at = _now()
        session.add(entry)
        session.commit()


def index_memory(memory_id: str) -> None:
    """Index an already-durable memory row in Chroma.

    Postgres is the source of truth. A Chroma failure updates metadata for
    observability and then re-raises so callers/tests can see the indexing
    failure without losing the durable row.
    """
    with get_session() as session:
        entry = session.get(MemoryEntry, memory_id)
        if not entry:
            raise ValueError(f"memory {memory_id} not found")
        content = entry.content
        metadata = _chroma_metadata(entry)

    try:
        vector = embed_texts([content])[0]
        get_memory_collection().upsert(
            ids=[memory_id],
            embeddings=[vector],
            documents=[content],
            metadatas=[metadata],
        )
    except Exception as exc:  # noqa: BLE001
        _mark_index_status(memory_id, indexed=False, error=f"{type(exc).__name__}: {exc}")
        raise
    _mark_index_status(memory_id, indexed=True)


def add_memory(
    content: str,
    *,
    kind: str,
    owner_id: str,
    task_id: str | None = None,
    importance: int = 3,
    meta: dict | None = None,
) -> str:
    memory_id = str(uuid.uuid4())
    with get_session() as session:
        entry = MemoryEntry(
            id=memory_id,
            owner_id=owner_id,
            task_id=task_id,
            kind=kind,
            content=content,
            importance=importance,
            meta={**(meta or {}), "chroma_indexed": False},
        )
        session.add(entry)
        session.commit()

    try:
        index_memory(memory_id)
    except Exception:
        # Durable memory has already been written. Callers that need to assert
        # the indexing failure can inspect MemoryEntry.meta["chroma_indexed"].
        pass

    return memory_id


def _status(entry: MemoryEntry) -> str:
    status = (entry.meta or {}).get("status")
    return str(status or "active")


def _is_active(entry: MemoryEntry) -> bool:
    return _status(entry) in ("", "active")


def retrieve_relevant(
    query: str,
    *,
    owner_id: str,
    k: int = 3,
    kind: str | None = None,
    similarity_floor: float = 0.3,
    include_archived: bool = False,
    mark_accessed: bool = False,
) -> list[RetrievedMemory]:
    """Retrieval ranks by a blend of semantic similarity and importance, not
    similarity alone — a highly important preference should surface even when
    it's a middling semantic match, which is why this isn't a raw Chroma query.
    Always scoped to owner_id -- see add_memory's note on why owner_id lives
    in Chroma metadata too, not just Postgres."""
    collection = get_memory_collection()
    if collection.count() == 0:
        return []

    vector = embed_texts([query])[0]
    conditions = [{"owner_id": owner_id}]
    if kind:
        conditions.append({"kind": kind})
    where = conditions[0] if len(conditions) == 1 else {"$and": conditions}
    results = collection.query(
        query_embeddings=[vector],
        n_results=min(k * 3, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    ids = results["ids"][0]
    if not ids:
        return []

    with get_session() as session:
        entries = session.exec(
            select(MemoryEntry).where(MemoryEntry.owner_id == owner_id, MemoryEntry.id.in_(ids))
        ).all()
        by_id = {entry.id: entry for entry in entries}

    retrieved: list[RetrievedMemory] = []
    ids = results["ids"][0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for mid, doc, meta, distance in zip(ids, documents, metadatas, distances):
        entry = by_id.get(mid)
        if not entry:
            continue
        if not include_archived and not _is_active(entry):
            continue
        if kind and entry.kind != kind:
            continue
        similarity = 1.0 - distance
        if similarity < similarity_floor:
            continue
        importance = int(entry.importance)
        weighted = 0.7 * similarity + 0.3 * (importance / 5)
        retrieved.append(
            RetrievedMemory(
                id=mid,
                content=entry.content or doc,
                kind=entry.kind,
                importance=importance,
                similarity=round(similarity, 3),
                weighted_score=round(weighted, 3),
            )
        )

    retrieved.sort(key=lambda m: m.weighted_score, reverse=True)
    top = retrieved[:k]

    if top and mark_accessed:
        with get_session() as session:
            for m in top:
                entry = session.get(MemoryEntry, m.id)
                if entry:
                    entry.last_accessed_at = datetime.now(timezone.utc)
                    session.add(entry)
            session.commit()

    return top


def reinforce_memory_use(memory_ids: list[str], *, owner_id: str) -> int:
    """Mark memories as used only after they are selected into model context.

    Raw Chroma candidates are deliberately not counted as usage; Phase 6D uses
    this function from the context builder after final selection.
    """
    if not memory_ids:
        return 0
    now = _now()
    with get_session() as session:
        entries = session.exec(
            select(MemoryEntry).where(
                MemoryEntry.owner_id == owner_id,
                MemoryEntry.id.in_(memory_ids),
            )
        ).all()
        updated = 0
        for entry in entries:
            if not _is_active(entry):
                continue
            meta = dict(entry.meta or {})
            usage_count = int(meta.get("usage_count") or meta.get("retrieval_count") or 0) + 1
            meta["usage_count"] = usage_count
            meta["retrieval_count"] = usage_count
            meta["last_used_at"] = now.isoformat()
            entry.meta = meta
            entry.last_accessed_at = now
            entry.updated_at = now
            session.add(entry)
            updated += 1
        session.commit()
        return updated


def _used_recently(meta: dict, *, now: datetime, stale_after: timedelta) -> bool:
    raw = meta.get("last_used_at")
    if not raw:
        return False
    try:
        last_used = datetime.fromisoformat(str(raw))
    except ValueError:
        return False
    if last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=timezone.utc)
    return now - last_used < stale_after


def archive_low_value_memories(
    owner_id: str,
    *,
    min_age_days: int = 90,
    stale_after_days: int = 30,
    importance_threshold: int = 2,
    now: datetime | None = None,
) -> int:
    """Archive, do not delete, stale low-value memories.

    Pinned decisions are never auto-archived merely due to age. The embedding
    and Postgres row remain recoverable; default retrieval excludes archived
    rows because Postgres remains the source of truth.
    """
    now = now or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    old_enough = now - timedelta(days=min_age_days)
    stale_after = timedelta(days=stale_after_days)
    archived = 0
    with get_session() as session:
        entries = session.exec(select(MemoryEntry).where(MemoryEntry.owner_id == owner_id)).all()
        for entry in entries:
            if entry.kind == "pinned_decision":
                continue
            if not _is_active(entry):
                continue
            if entry.importance > importance_threshold:
                continue
            created_at = entry.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if created_at > old_enough:
                continue
            meta = dict(entry.meta or {})
            usage_count = int(meta.get("usage_count") or meta.get("retrieval_count") or 0)
            if usage_count > 0 and _used_recently(meta, now=now, stale_after=stale_after):
                continue
            meta["status"] = "archived"
            meta["archived_at"] = now.isoformat()
            meta["archive_reason"] = "low_value_stale_unused"
            entry.meta = meta
            entry.updated_at = now
            session.add(entry)
            archived += 1
        session.commit()
    return archived


def prune_low_value_memories(owner_id: str, keep_top_n: int = 500) -> int:
    """Expiration: once long-term memory grows past keep_top_n entries, drop the
    least important, least recently accessed ones rather than growing forever.
    Scoped to one owner -- pruning globally would let one user's memory
    volume evict another user's entries."""
    with get_session() as session:
        all_entries = session.exec(select(MemoryEntry).where(MemoryEntry.owner_id == owner_id)).all()
        if len(all_entries) <= keep_top_n:
            return 0
        pinned = [entry for entry in all_entries if entry.kind == "pinned_decision"]
        candidates = [entry for entry in all_entries if entry.kind != "pinned_decision"]
        candidate_keep = max(0, keep_top_n - len(pinned))
        ranked = sorted(
            candidates, key=lambda e: (e.importance, e.last_accessed_at), reverse=True
        )
        to_drop = ranked[candidate_keep:]
        drop_ids = [e.id for e in to_drop]
        for entry in to_drop:
            session.delete(entry)
        session.commit()

    if drop_ids:
        get_memory_collection().delete(ids=drop_ids)
    return len(drop_ids)
