from rag.config import settings
from rag.generation.generate import format_sources
from rag.generation.prompts import VERIFICATION_PROMPT
from rag.generation.types import CitationVerification, GeneratedAnswer
from rag.llm import get_client


def verify_citations(
    query: str, generated: GeneratedAnswer, *, model: str | None = None
) -> CitationVerification:
    prompt = VERIFICATION_PROMPT.format(
        query=query,
        answer=generated.text,
        sources=format_sources(generated.sources),
    )
    client = get_client()
    completion = client.beta.chat.completions.parse(
        model=model or settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
        response_format=CitationVerification,
    )
    parsed = completion.choices[0].message.parsed
    return parsed or CitationVerification(completeness_score=1)
