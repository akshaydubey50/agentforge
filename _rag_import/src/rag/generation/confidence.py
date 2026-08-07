from rag.generation.types import CitationVerification, ConfidenceBreakdown, GeneratedAnswer


def compute_confidence(
    generated: GeneratedAnswer, verification: CitationVerification
) -> ConfidenceBreakdown:
    cited_sources = [s for s in generated.sources if s.index in generated.cited_indices]
    scored_sources = cited_sources or generated.sources
    # Rerank scores are 0-10; normalize to 0-1.
    retrieval_confidence = (
        sum(s.chunk.score for s in scored_sources) / (len(scored_sources) * 10)
        if scored_sources
        else 0.0
    )

    supported = sum(1 for c in verification.checks if c.supported)
    total_claims = len(verification.checks) + len(verification.uncited_factual_claims)
    citation_coverage = supported / total_claims if total_claims else 1.0

    completeness = verification.completeness_score / 5.0

    # Multiplicative, not additive: retrieval_confidence gates the score. An honest
    # "the sources don't cover this" answer vacuously scores citation_coverage=1.0
    # and completeness=1.0 (nothing to misattribute, honesty is complete) — an
    # additive formula would let that combination read as ~60% confident despite
    # having found nothing relevant. Multiplying means zero relevant sources means
    # zero overall confidence, regardless of how gracefully the model declined.
    overall = retrieval_confidence * (0.6 * citation_coverage + 0.4 * completeness)
    overall = max(0.0, min(1.0, overall))

    return ConfidenceBreakdown(
        retrieval_confidence=round(retrieval_confidence, 3),
        citation_coverage=round(citation_coverage, 3),
        completeness=round(completeness, 3),
        overall=round(overall, 3),
    )
