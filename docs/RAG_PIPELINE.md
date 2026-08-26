# Hybrid RAG Eval

A hybrid-search RAG system over an internal engineering knowledge base, built to demonstrate the
production concerns that separate a real RAG system from a single-PDF chatbot demo: hybrid
retrieval, chunking-strategy tradeoffs, citation verification, honest refusal, and a hard eval
number instead of a vibe check.

## Results

Evaluated against a 50-case hand-written golden set (30 single-hop, 12 multi-hop, 8 deliberately
out-of-scope) across all three chunking strategies:

| Strategy | Overall Correctness | Easy | Multi-hop | No-answer | Citation Precision | Citation Coverage | Retrieval Relevance |
|---|---|---|---|---|---|---|---|
| **fixed_overlap** | **98.0%** | 98.3% | 95.8% | 100% | 98.4% | 81.6% | 97.6% |
| structure_aware | 96.7% | 98.3% | 90.4% | 100% | 98.8% | 77.9% | 96.4% |
| semantic | 96.6% | 97.0% | 93.1% | 100% | 99.2% | 74.0% | 97.6% |

Zero errors across all 150 evaluations (50 cases × 3 strategies). Full per-case results and the
generated comparison report are in [`data/eval/results/`](data/eval/results/).

**What these numbers mean:**
- **Correctness** — an LLM judge scored whether each answer conveyed the expected facts (or, for
  the 8 out-of-scope questions, correctly declined instead of guessing).
- **Citation precision** — of the claims the system cited, 98%+ were actually supported by the
  source it cited. This is the number that matters for catching hallucinated citations.
- **100% no-answer accuracy, on every strategy** — asked "what is the CEO thinking about the Q4
  roadmap" against docs that never mention a CEO, the system said so instead of inventing an
  answer, every single time.

Fixed-size overlap chunking edged out the two "smarter" strategies here — see
[Design Decisions](#design-decisions) for why that's not actually surprising.

## Architecture

```
Query
  │
  ├─► Dense retrieval (OpenAI embeddings → ChromaDB, cosine)      ─┐
  │                                                                 ├─► Reciprocal Rank Fusion ─► top 20
  └─► Sparse retrieval (BM25 over the same chunk set)              ┘
                                                                          │
                                                                          ▼
                                                              LLM reranker (structured
                                                              relevance scoring) ─► top k
                                                                          │
                                                                          ▼
                                                        Grounded generation (numbered
                                                        sources, inline [n] citations)
                                                                          │
                                                                          ▼
                                                     LLM-judge citation verification
                                                     (per-claim supported/unsupported)
                                                                          │
                                                                          ▼
                                              Confidence = retrieval_confidence ×
                                              (citation_coverage, completeness)
```

Ingestion runs the same corpus through three independently indexed pipelines (one per chunking
strategy), each with its own ChromaDB collection and BM25 index, so retrieval quality can be
compared strategy-vs-strategy on identical source documents rather than guessed at.

### Why hybrid, not just dense

Dense embeddings miss exact-term matches (an error code, a config key, a specific header name);
BM25 misses paraphrases and semantic similarity. Reciprocal Rank Fusion combines both by **rank
position**, not raw score — dense cosine similarity and BM25 scores live on incomparable scales,
so fusing by score directly would silently let whichever retriever produces bigger numbers
dominate.

### Why an LLM reranker on top of RRF

RRF is a purely statistical fusion of two retrievers that don't understand the query. The
reranker is the first point in the pipeline that actually reads the candidate text against the
question and scores relevance — it's why the 20 RRF candidates can be trimmed to a precise top-k
instead of hoping the fusion math got the ordering right.

### Why citation verification is a separate LLM call, not part of generation

Asking a model to write an answer *and* judge its own citations in the same pass means it's
grading its own homework — a model that hallucinated a claim is likely to also hallucinate the
citation's validity. Verification runs as an independent pass over the finished answer plus the
actual source text, which is what makes citation precision (98%+) a meaningful number rather than
a self-report.

## Design Decisions

**Confidence is multiplicative, not additive.** The first version summed weighted
`retrieval_confidence + citation_coverage + completeness`. That broke on out-of-scope questions:
an honest "the sources don't cover this" answer has *zero* claims to misattribute, so
`citation_coverage` and `completeness` both default to a vacuous 1.0 — and the additive formula
scored that combination ~60% confident despite having found nothing relevant. Caught by a test
assertion, not a demo run. Fixed by making `retrieval_confidence` a multiplicative gate:
`overall = retrieval_confidence × (citation_coverage, completeness blend)`. Zero relevant sources
now means zero confidence, full stop, regardless of how gracefully the model declined. See
[`src/rag/generation/confidence.py`](src/rag/generation/confidence.py).

**Fixed-overlap chunking won the eval, not the "smarter" strategies.** Structure-aware and
semantic chunking produce more topically coherent chunks, but this corpus's policy documents pack
one fact per sentence at high density — fixed-overlap's denser, overlapping windows meant fewer
retrieval misses at chunk boundaries, at the cost of some redundancy (visible in its lower
citation coverage: more retrieved chunks partially overlap the same claim). On a corpus with
longer narrative sections, I'd expect structure-aware to win instead. The eval harness exists
specifically so this is a measured result, not an assumption.

**Near-duplicate detection runs pairwise within each ingestion batch** (cosine > 0.95), not
against the full existing index via an ANN query. At this corpus size (12 documents) that's the
right tradeoff; a corpus large enough for O(n²) to matter would need the check against a
vector-index query instead — noted as a scaling boundary, not solved speculatively.

**The eval judge and the generation model are the same model family (gpt-4o-mini).** This is a
known limitation, not an oversight: a judge sharing blind spots with the generator it's grading
can inflate scores. A stronger follow-up would score with a different model (e.g. Claude) than
the one generating answers.

## Corpus

Twelve synthetic internal-engineering documents (onboarding, API design, code review, security,
incident response, on-call, database migrations, feature flags, observability, postmortems,
performance testing, third-party integrations) spanning four file formats — markdown, plain text,
HTML, and PDF — to exercise the full multi-format loader. Written specifically for this project
rather than scraped from a public docs site, so every golden Q&A answer has a verifiable ground
truth I control. See [`data/raw/`](data/raw/).

## Running it

### Locally

```bash
python -m venv .venv
.venv/Scripts/activate  # or source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
pip install -e .
cp .env.example .env  # fill in OPENAI_API_KEY
python scripts/run_ingest.py --strategy all
uvicorn rag.main:app --reload
# in a second terminal:
streamlit run src/rag/dashboard.py
```

### Docker

```bash
docker compose up --build
```

This runs a one-shot `seed` service (ingests the corpus into all three strategies) before the
`api` (port 8000) and `dashboard` (port 8501) services start. Verified end-to-end: `seed` ingests
all 12 documents × 3 strategies inside the container, `api` responds on `/health`, `dashboard`
serves on 8501.

### Re-running the eval

```bash
python scripts/run_eval.py --strategy all
```

Takes ~8 minutes (150 LLM-judged evaluations across 3 strategies, 6-way concurrent). Results land
in `data/eval/results/`.

## API

- `POST /v1/ask` — `{question, strategy, top_k, use_reranker, sparse_weight}` → answer, citations,
  confidence breakdown, flagged unsupported claims
- `GET /v1/documents` — corpus listing
- `POST /v1/documents/upload` — saves one supported document and automatically rebuilds the
  production `semantic` index so the document is searchable immediately after upload
- `POST /v1/ingest` — manually rebuild ingestion for one or all strategies
- `GET /v1/strategies` — available chunking strategies
- Interactive docs at `/docs` (FastAPI's built-in OpenAPI UI)

## Tests

```bash
pytest
```

11 tests covering ingestion (multi-format loading, chunking correctness), retrieval (RRF fusion
logic, end-to-end hybrid retrieval against a known fact), generation (citation presence,
confidence behavior on in-scope vs. out-of-scope questions), and the API layer. These hit the real
OpenAI API rather than mocking it — consistent with the project's own point about verification
being meaningless without checking real behavior.
