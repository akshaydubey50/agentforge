import re

from rag.config import settings
from rag.generation.prompts import GENERATION_PROMPT
from rag.generation.types import GeneratedAnswer, Source
from rag.llm import complete
from rag.retrieval.types import RetrievedChunk

_CITATION_RE = re.compile(r"\[(\d+)\]")


def format_sources(sources: list[Source]) -> str:
    lines = []
    for source in sources:
        filename = source.chunk.metadata.get("filename", "unknown")
        lines.append(f"[{source.index}] ({filename}) {source.chunk.text}")
    return "\n\n".join(lines)


def generate_answer(
    query: str, retrieved_chunks: list[RetrievedChunk], *, model: str | None = None
) -> GeneratedAnswer:
    sources = [Source(index=i + 1, chunk=chunk) for i, chunk in enumerate(retrieved_chunks)]
    prompt = GENERATION_PROMPT.format(query=query, sources=format_sources(sources))
    text = complete(prompt, model=model or settings.llm_model)

    cited_indices = sorted({int(m) for m in _CITATION_RE.findall(text)})
    return GeneratedAnswer(text=text, sources=sources, cited_indices=cited_indices)
