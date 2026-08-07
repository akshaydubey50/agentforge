import numpy as np

from rag.ingest.chunking import ChunkingStrategy
from rag.ingest.index import load_bm25, tokenize
from rag.retrieval.types import RetrievedChunk


def sparse_search(query: str, strategy: ChunkingStrategy, k: int = 10) -> list[RetrievedChunk]:
    bm25, meta = load_bm25(strategy)
    scores = bm25.get_scores(tokenize(query))
    top_idx = np.argsort(scores)[::-1][:k]

    chunks: list[RetrievedChunk] = []
    for idx in top_idx:
        score = float(scores[idx])
        if score <= 0:
            continue
        entry = meta[idx]
        chunks.append(
            RetrievedChunk(
                chunk_id=entry["chunk_id"],
                text=entry["text"],
                metadata=entry["metadata"],
                score=score,
                sources=["sparse"],
            )
        )
    return chunks
