from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from rag.retrieval.types import RetrievedChunk


@dataclass
class Source:
    index: int
    """1-based index as shown to the model and user, e.g. [1]."""
    chunk: RetrievedChunk


@dataclass
class GeneratedAnswer:
    text: str
    sources: list[Source]
    cited_indices: list[int]


class CitationCheck(BaseModel):
    claim: str
    cited_source_numbers: list[int]
    supported: bool
    reasoning: str


class UncitedClaim(BaseModel):
    claim: str


class CitationVerification(BaseModel):
    checks: list[CitationCheck] = Field(default_factory=list)
    uncited_factual_claims: list[UncitedClaim] = Field(default_factory=list)
    completeness_score: int = Field(
        ge=1, le=5, description="1 = doesn't address the query, 5 = fully answers it using the sources"
    )


@dataclass
class ConfidenceBreakdown:
    retrieval_confidence: float
    citation_coverage: float
    completeness: float
    overall: float


@dataclass
class AnswerResult:
    query: str
    answer: GeneratedAnswer
    verification: CitationVerification
    confidence: ConfidenceBreakdown
    unsupported_claims: list[CitationCheck] = field(default_factory=list)
