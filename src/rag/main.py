from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from rag.auth import require_service_or_session, require_session
from rag.config import settings
from rag.generation.pipeline import answer_query
from rag.ingest.chunking import ChunkingStrategy
from rag.ingest.loaders import SUPPORTED_SUFFIXES, load_corpus, load_document
from rag.ingest.pipeline import run_ingest
from rag.llm import complete
from rag.retrieval.retriever import RetrievalConfig
from rag.security_headers import SecurityHeadersMiddleware
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

# allow_credentials=True so the session cookie (set by agentsys's Google
# sign-in, see rag/auth.py) rides along on the dashboard's fetches to this
# service too -- same reasoning as agentsys/main.py's CORS setup.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Outermost, so it also covers the preflight replies CORSMiddleware answers
# by itself -- see the matching note in agentsys/main.py.
app.add_middleware(SecurityHeadersMiddleware)


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
def list_strategies(_user_id: str = Depends(require_session)) -> dict[str, list[str]]:
    return {"strategies": [s.value for s in ChunkingStrategy]}


@app.get("/v1/documents", response_model=list[DocumentOut])
def list_documents(_user_id: str = Depends(require_session)) -> list[DocumentOut]:
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
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@app.get("/v1/documents/{filename}/raw")
def get_document_raw(filename: str, _user_id: str = Depends(require_session)) -> FileResponse:
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
    return FileResponse(
        target,
        media_type=media_type,
        filename=target.name,
        content_disposition_type="inline",
        # This route serves BYTES A USER UPLOADED, inline, on this API's own
        # origin -- which is the one place the app-wide CSP is not strict
        # enough. An uploaded .html or .svg rendered here would run script
        # with this origin's cookies in scope. `sandbox` drops the response
        # into an opaque origin with scripts disabled, and default-src 'none'
        # stops it fetching anything; together they make a hostile upload
        # inert while still letting a PDF or image display.
        #
        # Set on the response rather than as a middleware special case
        # because SecurityHeadersMiddleware uses setdefault -- a route that
        # states its own policy keeps it, which is exactly this situation.
        headers={
            "Content-Security-Policy": "default-src 'none'; sandbox; frame-ancestors 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.post("/v1/documents/upload", response_model=DocumentOut)
async def upload_document(file: UploadFile = File(...), _user_id: str = Depends(require_session)) -> DocumentOut:
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
def ingest(body: IngestRequest, _user_id: str = Depends(require_session)) -> IngestResponse:
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
def ask(body: AskRequest, _caller: str = Depends(require_service_or_session)) -> AskResponse:
    """Two legitimate callers, so require_service_or_session rather than
    require_session: agentsys's knowledge_search tool calls this
    server-to-server from the WORKER process (bearer token, no cookie), and
    a signed-in browser could reasonably call it too.

    This endpoint previously had no authentication at all while rag-api
    published a host port -- anyone who could reach it could query the whole
    corpus and spend LLM budget. See docs/ARCHITECTURE_AUDIT.md §7.3.

    TENANCY DEBT, unchanged by this fix and deliberately so: rag has no user
    model, so the corpus is shared across all users and this endpoint cannot
    scope retrieval to the caller. `_caller` is therefore proof that SOMEONE
    is authorised, not a filter on WHAT they may retrieve. Closing that
    needs per-document ownership through ingest, index and retrieval --
    a redesign, not a Phase-0 change. See §7.2."""
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
