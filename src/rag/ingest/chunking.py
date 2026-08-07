"""Three switchable chunking strategies: fixed-overlap, structure-aware, semantic."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag.ingest.loaders import Document
from rag.llm import embed_texts


class ChunkingStrategy(str, Enum):
    FIXED_OVERLAP = "fixed_overlap"
    STRUCTURE_AWARE = "structure_aware"
    SEMANTIC = "semantic"


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str
    position: int
    strategy: ChunkingStrategy
    metadata: dict = field(default_factory=dict)


def _fixed_overlap_chunks(text: str, chunk_size: int = 800, overlap: int = 150) -> list[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_text(text)


_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def _structure_aware_chunks(text: str, max_section_size: int = 1200) -> list[str]:
    """Split on markdown headers first; only fall back to fixed-size splitting
    within a section if that section alone is too large for one chunk."""
    matches = list(_HEADER_RE.finditer(text))
    if not matches:
        return _fixed_overlap_chunks(text, chunk_size=max_section_size, overlap=100)

    sections: list[str] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[start:end].strip()
        if section:
            sections.append(section)

    chunks: list[str] = []
    for section in sections:
        if len(section) <= max_section_size:
            chunks.append(section)
        else:
            chunks.extend(_fixed_overlap_chunks(section, chunk_size=max_section_size, overlap=100))
    return chunks


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z#])")


def _split_sentences(text: str) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    sentences: list[str] = []
    for para in paragraphs:
        sentences.extend(s.strip() for s in _SENTENCE_RE.split(para) if s.strip())
    return sentences


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / denom) if denom else 0.0


def _semantic_chunks(
    text: str, similarity_threshold: float = 0.55, max_chunk_chars: int = 1500
) -> list[str]:
    """Embed consecutive sentences; start a new chunk when topical similarity
    drops below the threshold or the running chunk exceeds the size cap."""
    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return [text] if text else []

    vectors = np.array(embed_texts(sentences))
    chunks: list[str] = []
    current = [sentences[0]]
    current_len = len(sentences[0])

    for i in range(1, len(sentences)):
        sim = _cosine(vectors[i - 1], vectors[i])
        would_exceed = current_len + len(sentences[i]) > max_chunk_chars
        if sim < similarity_threshold or would_exceed:
            chunks.append(" ".join(current))
            current = [sentences[i]]
            current_len = len(sentences[i])
        else:
            current.append(sentences[i])
            current_len += len(sentences[i])

    if current:
        chunks.append(" ".join(current))
    return chunks


_STRATEGY_FUNCS = {
    ChunkingStrategy.FIXED_OVERLAP: _fixed_overlap_chunks,
    ChunkingStrategy.STRUCTURE_AWARE: _structure_aware_chunks,
    ChunkingStrategy.SEMANTIC: _semantic_chunks,
}


def chunk_document(document: Document, strategy: ChunkingStrategy) -> list[Chunk]:
    texts = _STRATEGY_FUNCS[strategy](document.text)
    return [
        Chunk(
            chunk_id=f"{document.doc_id}_{strategy.value}_{i}",
            doc_id=document.doc_id,
            text=chunk_text,
            position=i,
            strategy=strategy,
            metadata={
                "source_path": document.source_path,
                "title": document.title,
                "format": document.format,
                "filename": document.metadata.get("filename", ""),
            },
        )
        for i, chunk_text in enumerate(texts)
    ]


def chunk_corpus(documents: list[Document], strategy: ChunkingStrategy) -> list[Chunk]:
    chunks: list[Chunk] = []
    for doc in documents:
        chunks.extend(chunk_document(doc, strategy))
    return chunks
