from dataclasses import dataclass, field


@dataclass
class RetrievedChunk:
    chunk_id: str
    text: str
    metadata: dict
    score: float
    sources: list[str] = field(default_factory=list)
    """Which retrievers surfaced this chunk, e.g. ['dense'], ['sparse'], ['dense', 'sparse']."""
