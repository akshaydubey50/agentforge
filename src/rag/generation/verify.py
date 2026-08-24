from rag.config import settings
from rag.generation.generate import format_sources
from rag.generation.prompts import VERIFICATION_PROMPT
from rag.generation.types import CitationVerification, GeneratedAnswer
from rag.llm import structured_complete


def verify_citations(
    query: str, generated: GeneratedAnswer, *, model: str | None = None
) -> CitationVerification:
    prompt = VERIFICATION_PROMPT.format(
        query=query,
        answer=generated.text,
        sources=format_sources(generated.sources),
    )
    parsed = structured_complete(prompt, CitationVerification, model=model or settings.llm_model)
    return parsed or CitationVerification(completeness_score=1)
