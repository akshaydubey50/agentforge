from rag.ingest.chunking import ChunkingStrategy
from rag.ingest.index import get_chroma_collection
from rag.llm import embed_texts
from rag.retrieval.types import RetrievedChunk


def dense_search(query: str, strategy: ChunkingStrategy, k: int = 10) -> list[RetrievedChunk]:
    collection = get_chroma_collection(strategy)
    query_vector = embed_texts([query])[0]

    results = collection.query(
        query_embeddings=[query_vector],
        n_results=k,
        include=["documents", "metadatas", "distances"],
    )

    chunks: list[RetrievedChunk] = []
    ids = results["ids"][0]
    documents = results["documents"][0]
    metadatas = results["metadatas"][0]
    distances = results["distances"][0]

    for chunk_id, text, metadata, distance in zip(ids, documents, metadatas, distances):
        # Collection uses cosine space: distance = 1 - cosine_similarity.
        similarity = 1.0 - distance
        chunks.append(
            RetrievedChunk(
                chunk_id=chunk_id,
                text=text,
                metadata=metadata,
                score=similarity,
                sources=["dense"],
            )
        )
    return chunks
