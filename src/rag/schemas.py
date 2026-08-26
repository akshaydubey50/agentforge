from pydantic import BaseModel


class AskRequest(BaseModel):
    question: str
    strategy: str = "structure_aware"
    top_k: int = 5
    use_reranker: bool = True
    sparse_weight: float = 1.0
    """Set to 0.0 to disable BM25's contribution and effectively run dense-only retrieval."""


class SourceOut(BaseModel):
    index: int
    filename: str
    title: str
    text: str
    score: float
    cited: bool


class ConfidenceOut(BaseModel):
    retrieval_confidence: float
    citation_coverage: float
    completeness: float
    overall: float


class UnsupportedClaimOut(BaseModel):
    claim: str
    cited_source_numbers: list[int]
    reasoning: str


class AskResponse(BaseModel):
    question: str
    answer: str
    cited_indices: list[int]
    sources: list[SourceOut]
    confidence: ConfidenceOut
    unsupported_claims: list[UnsupportedClaimOut]


class DocumentOut(BaseModel):
    filename: str
    format: str
    title: str


class IngestRequest(BaseModel):
    strategy: str = "all"


class IngestResultOut(BaseModel):
    strategy: str
    documents: int
    input_chunks: int
    indexed_chunks: int
    duplicates_dropped: int


class UploadDocumentResponse(BaseModel):
    document: DocumentOut
    index_result: IngestResultOut


class IngestResponse(BaseModel):
    results: list[IngestResultOut]
