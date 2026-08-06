from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from rag.config import settings
from rag.generation.pipeline import answer_query
from rag.ingest.chunking import ChunkingStrategy
from rag.ingest.loaders import load_corpus
from rag.ingest.pipeline import run_ingest
from rag.llm import complete
from rag.retrieval.retriever import RetrievalConfig
from rag.schemas import (
    AskRequest,
    AskResponse,
    ConfidenceOut,
    DocumentOut,
    IngestRequest,
    IngestResponse,
    IngestResultOut,
    SourceOut,
    UnsupportedClaimOut,
)

app = FastAPI(title="RAG Production Pipeline", version="0.1.0")


class PingRequest(BaseModel):
    prompt: str = "Say hello in one short sentence."


class PingResponse(BaseModel):
    output: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/ping", response_model=PingResponse)
def ping(body: PingRequest) -> PingResponse:
    """Phase 0 plumbing check: FastAPI -> OpenAI -> response, nothing smart yet."""
    return PingResponse(output=complete(body.prompt))


@app.get("/v1/strategies")
def list_strategies() -> dict[str, list[str]]:
    return {"strategies": [s.value for s in ChunkingStrategy]}


@app.get("/v1/documents", response_model=list[DocumentOut])
def list_documents() -> list[DocumentOut]:
    documents = load_corpus(settings.raw_data_dir)
    return [
        DocumentOut(filename=d.metadata.get("filename", ""), format=d.format, title=d.title)
        for d in documents
    ]


@app.post("/v1/ingest", response_model=IngestResponse)
def ingest(body: IngestRequest) -> IngestResponse:
    strategies = (
        list(ChunkingStrategy) if body.strategy == "all" else [_parse_strategy(body.strategy)]
    )
    results = []
    for strategy in strategies:
        stats = run_ingest(strategy)
        results.append(
            IngestResultOut(
                strategy=stats["strategy"],
                documents=stats["documents"],
                input_chunks=stats["input_chunks"],
                indexed_chunks=stats["indexed_chunks"],
                duplicates_dropped=stats["duplicates_dropped"],
            )
        )
    return IngestResponse(results=results)


@app.post("/v1/ask", response_model=AskResponse)
def ask(body: AskRequest) -> AskResponse:
    strategy = _parse_strategy(body.strategy)
    config = RetrievalConfig(
        strategy=strategy,
        top_k=body.top_k,
        use_reranker=body.use_reranker,
        sparse_weight=body.sparse_weight,
    )
    result = answer_query(body.question, config)

    sources = [
        SourceOut(
            index=s.index,
            filename=s.chunk.metadata.get("filename", ""),
            title=s.chunk.metadata.get("title", ""),
            text=s.chunk.text,
            score=s.chunk.score,
            cited=s.index in result.answer.cited_indices,
        )
        for s in result.answer.sources
    ]

    return AskResponse(
        question=result.query,
        answer=result.answer.text,
        cited_indices=result.answer.cited_indices,
        sources=sources,
        confidence=ConfidenceOut(
            retrieval_confidence=result.confidence.retrieval_confidence,
            citation_coverage=result.confidence.citation_coverage,
            completeness=result.confidence.completeness,
            overall=result.confidence.overall,
        ),
        unsupported_claims=[
            UnsupportedClaimOut(
                claim=c.claim,
                cited_source_numbers=c.cited_source_numbers,
                reasoning=c.reasoning,
            )
            for c in result.unsupported_claims
        ],
    )


def _parse_strategy(value: str) -> ChunkingStrategy:
    try:
        return ChunkingStrategy(value)
    except ValueError:
        valid = [s.value for s in ChunkingStrategy]
        raise HTTPException(status_code=400, detail=f"Unknown strategy '{value}'. Valid: {valid}")
