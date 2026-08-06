from pathlib import Path

from rag.ingest.chunking import ChunkingStrategy, chunk_document
from rag.ingest.loaders import load_corpus
from rag.config import settings


def test_load_corpus_covers_all_formats():
    documents = load_corpus(settings.raw_data_dir)
    formats = {doc.format for doc in documents}
    assert formats == {"markdown", "text", "html", "pdf"}
    assert len(documents) == 12


def test_fixed_overlap_chunks_respect_size_cap():
    documents = load_corpus(settings.raw_data_dir)
    doc = next(d for d in documents if "api_design" in d.source_path)
    chunks = chunk_document(doc, ChunkingStrategy.FIXED_OVERLAP)
    assert len(chunks) > 1
    assert all(len(c.text) <= 900 for c in chunks)


def test_structure_aware_chunks_align_with_headers():
    documents = load_corpus(settings.raw_data_dir)
    doc = next(d for d in documents if "api_design" in d.source_path)
    chunks = chunk_document(doc, ChunkingStrategy.STRUCTURE_AWARE)
    assert any(c.text.startswith("##") for c in chunks)
