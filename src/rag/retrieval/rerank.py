from pydantic import BaseModel, Field

from rag.config import settings
from rag.llm import get_client
from rag.retrieval.types import RetrievedChunk

_MAX_CANDIDATE_CHARS = 600


class CandidateScore(BaseModel):
    chunk_id: str
    relevance: int = Field(ge=0, le=10, description="0 = irrelevant, 10 = directly answers the query")


class RerankResult(BaseModel):
    scores: list[CandidateScore]


_RERANK_PROMPT = """You are a retrieval quality judge. Given a user query and a list of \
candidate passages, score how relevant each passage is to directly answering the query, \
from 0 (irrelevant) to 10 (directly and completely answers it).

Query: {query}

Candidates:
{candidates}

Return a score for every candidate ID listed above."""


def llm_rerank(
    query: str, candidates: list[RetrievedChunk], *, top_k: int = 5, model: str | None = None
) -> list[RetrievedChunk]:
    if not candidates:
        return []

    numbered = "\n\n".join(
        f"[{c.chunk_id}] {c.text[:_MAX_CANDIDATE_CHARS]}" for c in candidates
    )
    prompt = _RERANK_PROMPT.format(query=query, candidates=numbered)

    client = get_client()
    completion = client.beta.chat.completions.parse(
        model=model or settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
        response_format=RerankResult,
    )
    parsed = completion.choices[0].message.parsed

    score_by_id = {s.chunk_id: s.relevance for s in parsed.scores} if parsed else {}
    by_id = {c.chunk_id: c for c in candidates}

    ranked_ids = sorted(
        by_id,
        key=lambda cid: score_by_id.get(cid, -1),
        reverse=True,
    )

    reranked = []
    for cid in ranked_ids[:top_k]:
        chunk = by_id[cid]
        chunk.score = float(score_by_id.get(cid, 0))
        reranked.append(chunk)
    return reranked
