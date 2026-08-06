import os

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

import chromadb

from agentsys.config import settings

_client: chromadb.ClientAPI | None = None

COLLECTION_NAME = "agent_long_term_memory"


def get_chroma_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        _client = chromadb.HttpClient(
            host=settings.chroma_host,
            port=settings.chroma_port,
            settings=chromadb.Settings(anonymized_telemetry=False),
        )
    return _client


def get_memory_collection():
    client = get_chroma_client()
    return client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
