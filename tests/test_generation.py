from rag.generation.pipeline import answer_query
from rag.ingest.chunking import ChunkingStrategy
from rag.retrieval.retriever import RetrievalConfig

_CFG = RetrievalConfig(strategy=ChunkingStrategy.STRUCTURE_AWARE, top_k=4)


def test_in_scope_question_is_cited_and_confident():
    result = answer_query(
        "What batch size should database backfills use?", _CFG
    )
    assert "[" in result.answer.text
    assert result.answer.cited_indices
    assert result.confidence.overall > 0.6
    assert result.unsupported_claims == []


def test_out_of_scope_question_declines_without_hallucinating():
    result = answer_query(
        "What is the CEO of this company thinking about the Q4 roadmap?", _CFG
    )
    assert result.confidence.retrieval_confidence < 0.3
    assert result.confidence.overall < 0.5
