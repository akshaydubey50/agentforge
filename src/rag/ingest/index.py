"""Index chunks into ChromaDB (dense) and BM25 (sparse), kept in sync, with
near-duplicate detection before insertion."""

from __future__ import annotations

import json
import os
import pickle
import re

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import chromadb
import numpy as np
from rank_bm25 import BM25Okapi

from rag.config import settings
from rag.ingest.chunking import Chunk, ChunkingStrategy
from rag.llm import embed_texts

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom else 0.0


def _drop_near_duplicates(
    chunks: list[Chunk], vectors: np.ndarray, threshold: float = 0.95
) -> tuple[list[Chunk], np.ndarray]:
    """O(n^2) pairwise check against already-kept chunks. Fine at this corpus
    size; a large corpus would use an ANN index for this check instead."""
    kept_idx: list[int] = []
    for i in range(len(chunks)):
        is_dup = any(_cosine(vectors[i], vectors[j]) > threshold for j in kept_idx)
        if not is_dup:
            kept_idx.append(i)
    return [chunks[i] for i in kept_idx], vectors[kept_idx]


def _chroma_client() -> chromadb.ClientAPI:
    return chromadb.PersistentClient(
        path=settings.chroma_persist_dir,
        settings=chromadb.Settings(anonymized_telemetry=False),
    )


def get_chroma_collection(strategy: ChunkingStrategy):
    client = _chroma_client()
    return client.get_or_create_collection(
        name=f"rag_{strategy.value}",
        metadata={"hnsw:space": "cosine"},
    )


def reset_chroma_collection(strategy: ChunkingStrategy) -> None:
    client = _chroma_client()
    try:
        client.delete_collection(name=f"rag_{strategy.value}")
    except Exception:
        pass


def build_index(chunks: list[Chunk], strategy: ChunkingStrategy) -> dict:
    texts = [c.text for c in chunks]
    vectors = np.array(embed_texts(texts))

    deduped_chunks, deduped_vectors = _drop_near_duplicates(chunks, vectors)

    collection = get_chroma_collection(strategy)
    collection.add(
        ids=[c.chunk_id for c in deduped_chunks],
        embeddings=deduped_vectors.tolist(),
        documents=[c.text for c in deduped_chunks],
        metadatas=[
            {**c.metadata, "doc_id": c.doc_id, "position": c.position}
            for c in deduped_chunks
        ],
    )

    tokenized = [tokenize(c.text) for c in deduped_chunks]
    bm25 = BM25Okapi(tokenized)

    settings.processed_data_dir.mkdir(parents=True, exist_ok=True)
    bm25_path = settings.processed_data_dir / f"bm25_{strategy.value}.pkl"
    with open(bm25_path, "wb") as f:
        pickle.dump(bm25, f)

    bm25_meta = [
        {
            "chunk_id": c.chunk_id,
            "text": c.text,
            "metadata": {**c.metadata, "doc_id": c.doc_id, "position": c.position},
        }
        for c in deduped_chunks
    ]
    meta_path = settings.processed_data_dir / f"bm25_meta_{strategy.value}.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(bm25_meta, f, indent=2)

    return {
        "strategy": strategy.value,
        "input_chunks": len(chunks),
        "indexed_chunks": len(deduped_chunks),
        "duplicates_dropped": len(chunks) - len(deduped_chunks),
    }


def load_bm25(strategy: ChunkingStrategy) -> tuple[BM25Okapi, list[dict]]:
    bm25_path = settings.processed_data_dir / f"bm25_{strategy.value}.pkl"
    meta_path = settings.processed_data_dir / f"bm25_meta_{strategy.value}.json"
    with open(bm25_path, "rb") as f:
        bm25 = pickle.load(f)
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    return bm25, meta
