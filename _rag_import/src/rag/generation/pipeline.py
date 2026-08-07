from rag.generation.confidence import compute_confidence
from rag.generation.generate import generate_answer
from rag.generation.types import AnswerResult
from rag.generation.verify import verify_citations
from rag.retrieval.retriever import RetrievalConfig, hybrid_retrieve


def answer_query(query: str, retrieval_config: RetrievalConfig | None = None) -> AnswerResult:
    retrieved = hybrid_retrieve(query, retrieval_config)
    generated = generate_answer(query, retrieved)
    verification = verify_citations(query, generated)
    confidence = compute_confidence(generated, verification)

    return AnswerResult(
        query=query,
        answer=generated,
        verification=verification,
        confidence=confidence,
        unsupported_claims=[c for c in verification.checks if not c.supported],
    )
