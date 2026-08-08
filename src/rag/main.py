from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from rag.config import settings
from rag.generation.pipeline import answer_query
from rag.ingest.chunking import ChunkingStrategy
from rag.ingest.loaders import SUPPORTED_SUFFIXES, load_corpus, load_document
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

# Not auth: matching agentsys/main.py's CORS setup -- see that file's comment
# for the reasoning. Same open-until-auth-exists caveat applies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


_RAW_DOCUMENT_MEDIA_TYPES = {
    ".md": "text/plain; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".pdf": "application/pdf",
}


@app.get("/v1/documents/{filename}/raw")
def get_document_raw(filename: str) -> FileResponse:
    """Serves a corpus document's actual bytes so the UI can link straight to
    it -- same traversal guard as file_io's tool (agentsys side): resolve and
    check is_relative_to() against the corpus root before ever touching the
    filesystem, rather than trusting the path segment."""
    raw_dir = settings.raw_data_dir.resolve()
    target = (raw_dir / Path(filename).name).resolve()
    if target != raw_dir and not target.is_relative_to(raw_dir):
        raise HTTPException(status_code=400, detail="invalid filename")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="document not found")

    media_type = _RAW_DOCUMENT_MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(target, media_type=media_type, filename=target.name, content_disposition_type="inline")


@app.post("/v1/documents/upload", response_model=DocumentOut)
async def upload_document(file: UploadFile = File(...)) -> DocumentOut:
    """Saves an uploaded file into the same raw-corpus directory the old
    Streamlit dashboard (simple_upload.py) writes into directly -- that
    dashboard runs server-side so it can touch the filesystem straight from
    the browser's uploaded bytes, but a real browser client can't, so this
    is the REST equivalent of that same write. Doesn't index anything by
    itself; call /v1/ingest afterwards (same as the old dashboard's
    two-step Upload then Reindex flow).
    """
    filename = Path(file.filename or "").name  # strip any path components -- traversal guard
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported file type '{suffix}' -- allowed: {sorted(SUPPORTED_SUFFIXES)}",
        )

    settings.raw_data_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.raw_data_dir / filename
    dest.write_bytes(await file.read())

    doc = load_document(dest)
    return DocumentOut(filename=doc.metadata.get("filename", filename), format=doc.format, title=doc.title)


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
