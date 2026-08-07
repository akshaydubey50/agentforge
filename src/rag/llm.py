from openai import OpenAI

from rag.config import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Add it to your local .env file."
            )
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def complete(prompt: str, *, model: str | None = None) -> str:
    client = get_client()
    response = client.chat.completions.create(
        model=model or settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or ""


def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Batch-embed texts. OpenAI accepts up to ~2048 inputs per call; we chunk at 256 to be safe."""
    if not texts:
        return []
    client = get_client()
    embed_model = model or settings.embedding_model
    vectors: list[list[float]] = []
    batch_size = 256
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        response = client.embeddings.create(model=embed_model, input=batch)
        vectors.extend(item.embedding for item in response.data)
    return vectors
