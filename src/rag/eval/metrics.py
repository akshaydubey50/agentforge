from pydantic import BaseModel, Field

from rag.config import settings
from rag.eval.golden_dataset import GoldenCase
from rag.generation.types import AnswerResult
from rag.llm import structured_complete

_JUDGE_PROMPT = """You are grading a RAG system's answer against expected key facts.

Question: {question}

Expected key facts (the answer should convey these if they exist; if this list is EMPTY, the \
correct behavior is to decline / say the information isn't available in the sources — that is \
what correctness=1.0 means for this case, NOT producing a fabricated answer):
{expected_facts}

Actual answer:
{answer}

Score correctness from 0.0 to 1.0:
- 1.0: all expected facts are conveyed correctly (or, for an empty expected-facts list, the \
  answer correctly declines without fabricating anything)
- partial credit for conveying some but not all expected facts, or minor inaccuracy
- 0.0: expected facts are missing, wrong, or (for an empty list) the answer hallucinates an \
  answer instead of declining
"""


class CorrectnessJudgment(BaseModel):
    correctness: float = Field(ge=0.0, le=1.0)
    reasoning: str


def judge_correctness(case: GoldenCase, result: AnswerResult, *, model: str | None = None) -> CorrectnessJudgment:
    prompt = _JUDGE_PROMPT.format(
        question=case.question,
        expected_facts=case.expected_facts or "(none — correct answer is to decline)",
        answer=result.answer.text,
    )
    parsed = structured_complete(prompt, CorrectnessJudgment, model=model or settings.llm_model)
    return parsed or CorrectnessJudgment(correctness=0.0, reasoning="judge returned no output")


def retrieval_relevance(case: GoldenCase, result: AnswerResult) -> float | None:
    """Fraction of expected source files that appear among the sources the model
    actually cited. None (excluded from aggregates) for no-answer cases, where
    there's no expected source to recall."""
    if not case.expected_source_files:
        return None
    cited_files = {
        s.chunk.metadata.get("filename")
        for s in result.answer.sources
        if s.index in result.answer.cited_indices
    }
    hits = sum(1 for f in case.expected_source_files if f in cited_files)
    return hits / len(case.expected_source_files)


def citation_precision(result: AnswerResult) -> float:
    """Of the claims that WERE cited, what fraction are actually supported by
    their cited source (precision — catches mis-citation / hallucinated support)."""
    checks = result.verification.checks
    if not checks:
        return 1.0
    supported = sum(1 for c in checks if c.supported)
    return supported / len(checks)
