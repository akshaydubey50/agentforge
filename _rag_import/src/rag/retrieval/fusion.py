from rag.retrieval.types import RetrievedChunk


def reciprocal_rank_fusion(
    dense_results: list[RetrievedChunk],
    sparse_results: list[RetrievedChunk],
    *,
    rrf_k: int = 60,
    dense_weight: float = 1.0,
    sparse_weight: float = 1.0,
) -> list[RetrievedChunk]:
    """Fuse two ranked lists by rank position, not raw score, so dense
    (cosine) and sparse (BM25) results are comparable despite different
    scales. Standard formula: score += weight / (rrf_k + rank)."""
    fused: dict[str, RetrievedChunk] = {}
    scores: dict[str, float] = {}

    for rank, chunk in enumerate(dense_results):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + dense_weight / (rrf_k + rank)
        fused[chunk.chunk_id] = chunk

    for rank, chunk in enumerate(sparse_results):
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + sparse_weight / (rrf_k + rank)
        if chunk.chunk_id in fused:
            existing = fused[chunk.chunk_id]
            existing.sources = sorted(set(existing.sources) | {"sparse"})
        else:
            fused[chunk.chunk_id] = chunk

    ranked_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
    results = []
    for cid in ranked_ids:
        chunk = fused[cid]
        chunk.score = scores[cid]
        results.append(chunk)
    return results
