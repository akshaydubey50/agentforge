from rag.ingest.chunking import ChunkingStrategy
from rag.retrieval.fusion import reciprocal_rank_fusion
from rag.retrieval.retriever import RetrievalConfig, hybrid_retrieve
from rag.retrieval.types import RetrievedChunk


def test_reciprocal_rank_fusion_favors_agreement():
    dense = [
        RetrievedChunk("a", "text a", {}, score=0.9, sources=["dense"]),
        RetrievedChunk("b", "text b", {}, score=0.8, sources=["dense"]),
    ]
    sparse = [
        RetrievedChunk("b", "text b", {}, score=5.0, sources=["sparse"]),
        RetrievedChunk("c", "text c", {}, score=4.0, sources=["sparse"]),
    ]
    fused = reciprocal_rank_fusion(dense, sparse)
    # "b" appears in both rankers, so it should outrank "a" (dense-only, rank 0)
    # despite "a" having the single best individual rank.
    ids = [c.chunk_id for c in fused]
    assert ids.index("b") < ids.index("a")


def test_hybrid_retrieve_finds_the_right_fact():
    results = hybrid_retrieve(
        "How long is the sunset window before a deprecated API version is removed?",
        RetrievalConfig(strategy=ChunkingStrategy.STRUCTURE_AWARE, top_k=3),
    )
    assert results
    assert any("90-day" in r.text or "90 day" in r.text for r in results[:1])
