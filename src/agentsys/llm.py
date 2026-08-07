from openai import OpenAI

from agentsys.config import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is not set. Add it to your local .env file.")
        _client = OpenAI(api_key=settings.openai_api_key)
    return _client


def complete(prompt: str, *, model: str | None = None) -> tuple[str, object]:
    """Returns (text, completion) rather than just text -- callers that need
    to record cost (see agentsys.cost) need the raw completion for its
    model/usage fields; callers that don't just take the first element."""
    client = get_client()
    response = client.chat.completions.create(
        model=model or settings.llm_model,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content or "", response


def embed_texts(texts: list[str], *, model: str = "text-embedding-3-small") -> list[list[float]]:
    if not texts:
        return []
    client = get_client()
    response = client.embeddings.create(model=model, input=texts)
    return [item.embedding for item in response.data]
