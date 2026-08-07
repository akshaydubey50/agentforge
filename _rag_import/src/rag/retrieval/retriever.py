from dataclasses import dataclass

from rag.ingest.chunking import ChunkingStrategy
from rag.retrieval.dense import dense_search
from rag.retrieval.fusion import reciprocal_rank_fusion
from rag.retrieval.rerank import llm_rerank
from rag.retrieval.sparse import sparse_search
from rag.retrieval.types import RetrievedChunk


@dataclass
class RetrievalConfig:
    strategy: ChunkingStrategy = ChunkingStrategy.STRUCTURE_AWARE
    dense_k: int = 10
    sparse_k: int = 10
    fused_k: int = 20
    top_k: int = 5
    dense_weight: float = 1.0
    sparse_weight: float = 1.0
    use_reranker: bool = True


def hybrid_retrieve(query: str, config: RetrievalConfig | None = None) -> list[RetrievedChunk]:
    config = config or RetrievalConfig()

    dense_results = dense_search(query, config.strategy, k=config.dense_k)
    sparse_results = sparse_search(query, config.strategy, k=config.sparse_k)

    fused = reciprocal_rank_fusion(
        dense_results,
        sparse_results,
        dense_weight=config.dense_weight,
        sparse_weight=config.sparse_weight,
    )[: config.fused_k]

    if not config.use_reranker:
        return fused[: config.top_k]

    return llm_rerank(query, fused, top_k=config.top_k)
