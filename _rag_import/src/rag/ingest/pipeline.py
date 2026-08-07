"""Orchestrates: load corpus -> chunk -> dedupe -> index (dense + sparse)."""

from __future__ import annotations

from rag.config import settings
from rag.ingest.chunking import ChunkingStrategy, chunk_corpus
from rag.ingest.index import build_index, reset_chroma_collection
from rag.ingest.loaders import load_corpus


def run_ingest(strategy: ChunkingStrategy, reset: bool = True) -> dict:
    documents = load_corpus(settings.raw_data_dir)
    if not documents:
        raise RuntimeError(f"No documents found in {settings.raw_data_dir}")

    chunks = chunk_corpus(documents, strategy)
    if reset:
        reset_chroma_collection(strategy)
    stats = build_index(chunks, strategy)
    stats["documents"] = len(documents)
    return stats
