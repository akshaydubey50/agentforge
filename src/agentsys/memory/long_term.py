import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

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


def add_memory(
    content: str, *, kind: str, task_id: str | None = None, importance: int = 3
) -> str:
    memory_id = str(uuid.uuid4())
    vector = embed_texts([content])[0]

    collection = get_memory_collection()
    collection.add(
        ids=[memory_id],
        embeddings=[vector],
        documents=[content],
        metadatas=[{"kind": kind, "task_id": task_id or "", "importance": importance}],
    )

    with get_session() as session:
        entry = MemoryEntry(
            id=memory_id,
            task_id=task_id,
            kind=kind,
            content=content,
            importance=importance,
        )
        session.add(entry)
        session.commit()

    return memory_id


def retrieve_relevant(
    query: str, *, k: int = 3, kind: str | None = None, similarity_floor: float = 0.3
) -> list[RetrievedMemory]:
    """Retrieval ranks by a blend of semantic similarity and importance, not
    similarity alone — a highly important preference should surface even when
    it's a middling semantic match, which is why this isn't a raw Chroma query."""
    collection = get_memory_collection()
    if collection.count() == 0:
        return []

    vector = embed_texts([query])[0]
    where = {"kind": kind} if kind else None
    results = collection.query(
        query_embeddings=[vector],
        n_results=min(k * 3, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    retrieved: list[RetrievedMemory] = []
    ids = results["ids"][0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for mid, doc, meta, distance in zip(ids, documents, metadatas, distances):
        similarity = 1.0 - distance
        if similarity < similarity_floor:
            continue
        importance = int(meta.get("importance", 3))
        weighted = 0.7 * similarity + 0.3 * (importance / 5)
        retrieved.append(
            RetrievedMemory(
                id=mid,
                content=doc,
                kind=meta.get("kind", ""),
                importance=importance,
                similarity=round(similarity, 3),
                weighted_score=round(weighted, 3),
            )
        )

    retrieved.sort(key=lambda m: m.weighted_score, reverse=True)
    top = retrieved[:k]

    if top:
        with get_session() as session:
            for m in top:
                entry = session.get(MemoryEntry, m.id)
                if entry:
                    entry.last_accessed_at = datetime.now(timezone.utc)
                    session.add(entry)
            session.commit()

    return top


def prune_low_value_memories(keep_top_n: int = 500) -> int:
    """Expiration: once long-term memory grows past keep_top_n entries, drop the
    least important, least recently accessed ones rather than growing forever."""
    with get_session() as session:
        all_entries = session.exec(select(MemoryEntry)).all()
        if len(all_entries) <= keep_top_n:
            return 0
        ranked = sorted(
            all_entries, key=lambda e: (e.importance, e.last_accessed_at), reverse=True
        )
        to_drop = ranked[keep_top_n:]
        drop_ids = [e.id for e in to_drop]
        for entry in to_drop:
            session.delete(entry)
        session.commit()

    if drop_ids:
        get_memory_collection().delete(ids=drop_ids)
    return len(drop_ids)
